"""Operation registry: `kind → entrypoint` (architecture §5.4).

An entrypoint is the thin app-side wrapper over one `sidecar.modules.*` bounded
operation. It receives an `OperationContext` (the durable input snapshot + a
resolved engine for LLM kinds) and returns an `OperationOutcome` (result_ref +
usage + engine/model for the ledger). The runner never knows what a kind *does*
— only this contract.

**Core-storage boundary.** This commit ships the contract and an empty default
registry: real kinds (scan/score/tailor/cover/…) register here as their module
commits land (`docs/internal/roadmap.md` §7.2 #5+). The core tests exercise the
runner with fake entrypoints only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING, Any

from .engines import EngineNotConfiguredError, ResolvedEngine

if TYPE_CHECKING:
    from ..db import Database

PublishFn = Callable[[dict[str, Any]], None]


@dataclass
class OperationContext:
    """Everything an entrypoint needs to run one operation.

    `db` + `operation_id` let entrypoints persist their results — scan writes
    `Job` rows, score writes a `JobScore`, tailor/cover fill their pre-created
    `Artifact` (found by `operation_id`). Both are `None` under the
    fake-entrypoint runner tests, which never touch storage."""

    kind: str
    input_snapshot: dict[str, Any]
    engine: ResolvedEngine | None = None
    db: Database | None = None
    operation_id: str | None = None
    # Lets an entrypoint stream its own typed sub-events onto the SSE hub
    # (the Applier live-modal substream). None under the fake-entrypoint tests.
    publish: PublishFn | None = None


@dataclass
class OperationOutcome:
    """What an entrypoint hands back for the ledger + result pointer."""

    result_ref: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    engine: str | None = None
    model: str | None = None


Entrypoint = Callable[[OperationContext], OperationOutcome]


class UnknownOperationKind(Exception):
    """No entrypoint registered for this kind."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"no entrypoint registered for operation kind {kind!r}")


class OperationRegistry:
    def __init__(self, entries: dict[str, Entrypoint] | None = None) -> None:
        self._entries: dict[str, Entrypoint] = dict(entries or {})

    def register(self, kind: str, entrypoint: Entrypoint) -> None:
        self._entries[kind] = entrypoint

    def resolve(self, kind: str) -> Entrypoint:
        entrypoint = self._entries.get(kind)
        if entrypoint is None:
            raise UnknownOperationKind(kind)
        return entrypoint

    def kinds(self) -> frozenset[str]:
        return frozenset(self._entries)


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if is_dataclass(usage) and not isinstance(usage, type):
        return asdict(usage)
    if isinstance(usage, dict):
        return usage
    return None


# ---------------------------------------------------------------------------
# The real wrappers (app → modules import is allowed and correct).
# ---------------------------------------------------------------------------


def _require_engine(ctx: OperationContext) -> ResolvedEngine:
    if ctx.engine is None:
        raise EngineNotConfiguredError(ctx.kind)
    return ctx.engine


def scan_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Zero-LLM job scan (Scraper module) → persisted `Job` rows. No engine."""
    from sidecar.modules.scraper import scan

    from .persistence import (
        apply_brave_budget,
        load_scraper_credentials,
        persist_scan,
        record_brave_usage,
        resolve_portals,
        resolve_scan_prefs,
        scan_usage,
        with_credentials,
    )

    snap = ctx.input_snapshot
    if ctx.db is not None:
        with ctx.db.repos() as repos:
            portals = resolve_portals(snap, repos)
            prefs = resolve_scan_prefs(snap, repos=repos, portals=portals)
            # Sealed BYO keys (Apify/Brave) open here, into memory only — the
            # durable snapshot and result_ref never carry a secret.
            prefs = with_credentials(prefs, portals, load_scraper_credentials(repos))
            # Free-tier discipline: past ~2,000 Brave queries this month, the
            # Brave source sits out until the month rolls over.
            prefs = apply_brave_budget(prefs, portals, repos)
    else:
        portals = snap["portals_config"]
        prefs = resolve_scan_prefs(snap)
    result = scan(portals, prefs=prefs)
    result_ref = persist_scan(ctx.db, result)
    record_brave_usage(ctx.db, result_ref)
    return OperationOutcome(result_ref=result_ref, usage=scan_usage(result))


def cleanup_trash_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """The daily entity-lifecycle maintenance tick (zero-LLM, zero-network,
    DB-only). Every window reads the user-configurable lifecycle settings
    (FR-SYS-05); the defaults preserve prior behavior:

    - **Trash TTL** (FR-SYS-04): tombstone + delete Trashed jobs past the window
      (default 7 days).
    - **Expired aging** (FR-SYS-03): grey active jobs at 14 days, hard-delete
      Expired ones (no tombstone) at 30 days.
    - **Archived-application purge** (FR-SYS-06): permanently remove archived
      tracker cards past the window (default 30 days).

    The prior repository also purges archived contacts here; that stage returns
    with the Referral Outreach commits.
    """
    from ..lifecycle import LIFECYCLE_DEFAULTS, resolve_lifecycle
    from .persistence import (
        age_expired_jobs,
        evict_stale_trash,
        purge_archived_applications,
    )

    settings = None
    if ctx.db is not None:
        with ctx.db.repos() as repos:
            settings = resolve_lifecycle(repos.preferences.get_or_create())
    settings = settings or dict(LIFECYCLE_DEFAULTS)

    tombstoned = evict_stale_trash(ctx.db, ttl_days=settings["trashed_jobs_purge_days"])
    aged = age_expired_jobs(ctx.db)
    purged_apps = purge_archived_applications(
        ctx.db, retention_days=settings["archived_applications_purge_days"]
    )
    return OperationOutcome(
        result_ref={
            "tombstoned_count": len(tombstoned),
            "job_ids": tombstoned,
            "expired_count": len(aged["expired"]),
            "expired_deleted_count": len(aged["deleted"]),
            "expired_ids": aged["expired"],
            "expired_deleted_ids": aged["deleted"],
            "purged_applications_count": len(purged_apps),
            "purged_application_ids": purged_apps,
        }
    )


def _persist_score(
    ctx: OperationContext,
    *,
    job_id: str,
    profile_version: int,
    score_0_100: int,
    reasons: list[str],
    breakdown_md: str,
    scorer_impl: str,
    feed_priority_stats: bool,
) -> str | None:
    """Upsert one `JobScore` row; optionally feed the running priority
    distribution (FR-TR-09) exactly once per *new* score of that impl — a
    recompute of an existing cache key must not double count."""
    if ctx.db is None:
        return None
    from ..priority import STATS_KEY, welford_update

    with ctx.db.repos() as repos:
        is_new_score = (
            repos.job_scores.get_cached(job_id, profile_version, scorer_impl) is None
        )
        row = repos.job_scores.upsert(
            job_id=job_id,
            profile_version=profile_version,
            score_0_100=score_0_100,
            reasons=reasons,
            breakdown_md=breakdown_md,
            scorer_impl=scorer_impl,
            operation_id=ctx.operation_id,
        )
        if feed_priority_stats and is_new_score:
            prefs = repos.preferences.get_or_create()
            thresholds = dict(prefs.thresholds or {})
            thresholds[STATS_KEY] = welford_update(
                thresholds.get(STATS_KEY), float(score_0_100)
            )
            repos.preferences.update(thresholds=thresholds)
        return row.id


def _ensure_keyword_floor(
    ctx: OperationContext,
    *,
    job_id: str | None,
    profile_version: int,
    master_md: str,
    job_text: str,
) -> None:
    """Guarantee a keyword score exists for this (job, version) — idempotent:
    a no-op if the floor is already there (the usual case, since the post-scan
    and boot floor passes run first). Pure offline compute (~0.5 ms, no
    network). Best-effort: a compute error is logged, never raised — the floor
    is a safety net, not the caller's operation."""
    from sidecar.modules.scorer.deterministic import score_deterministic

    from .persistence import SCORER_IMPL_DETERMINISTIC

    if ctx.db is None or job_id is None:
        return
    with ctx.db.repos() as repos:
        if repos.job_scores.get_cached(job_id, profile_version, SCORER_IMPL_DETERMINISTIC):
            return
    try:
        det = score_deterministic(master_md, job_text)
        _persist_score(
            ctx,
            job_id=job_id,
            profile_version=profile_version,
            score_0_100=det.score,
            reasons=list(det.reasons),
            breakdown_md=det.breakdown_md,
            scorer_impl=SCORER_IMPL_DETERMINISTIC,
            feed_priority_stats=False,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "keyword floor failed for job_id=%s", job_id
        )


def score_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Fit-score one job → a cached `JobScore`.

    Two modes (Settings → Scoring, maintainer design 2026-07-22):
    - "keyword": the zero-LLM keyword scorer — free, instant, keyless. This IS
      the score.
    - "llm" (default): the routed-engine Scorer, an UPGRADE over the keyword
      floor every job already carries (`_ensure_keyword_floor`, run post-scan
      and at boot). On success the LLM score outranks the floor (display
      precedence LLM > keyword). On FAILURE the op raises — it stays failed and
      retryable in Logs, is not auto-retried, and the grey keyword floor is the
      fallback the board shows (ensured here in case this op raced the floor
      pass). No hidden re-scoring; nothing to fall back below keyword.
    """
    from ..prompt_overrides import get_override
    from .persistence import SCORER_IMPL, SCORER_IMPL_DETERMINISTIC, load_job_and_master

    snap = ctx.input_snapshot
    job_id = snap.get("job_id")

    mode = "llm"
    if ctx.db is not None:
        with ctx.db.repos() as repos:
            job_text, master_md, profile_version = load_job_and_master(repos, snap)
            prefs = repos.preferences.get_or_create()
            mode = str((prefs.thresholds or {}).get("scoring_mode") or "llm")
    else:
        job_text, master_md, profile_version = snap["job"], snap["master_md"], 0
        mode = str(snap.get("scoring_mode") or "llm")

    if mode == "keyword":
        from sidecar.modules.scorer.deterministic import score_deterministic

        det = score_deterministic(master_md, job_text)
        score_id = None
        if job_id is not None:
            score_id = _persist_score(
                ctx,
                job_id=job_id,
                profile_version=profile_version,
                score_0_100=det.score,
                reasons=list(det.reasons),
                breakdown_md=det.breakdown_md,
                scorer_impl=SCORER_IMPL_DETERMINISTIC,
                # In keyword mode these ARE the displayed scores — they drive
                # the priority distribution.
                feed_priority_stats=True,
            )
        return OperationOutcome(
            result_ref={"score": det.score, "job_id": job_id, "score_id": score_id},
            usage=None,
            engine="on-device",
            model="keyword",
        )

    resolved = _require_engine(ctx)
    from sidecar.modules.scorer.scorer import score

    try:
        result = score(
            master_md, job_text, engine=resolved.engine, skill_md=get_override("score")
        )
    except Exception:
        # The keyword floor is the fallback; guarantee it exists (idempotent)
        # then re-raise so the op stays failed + retryable.
        _ensure_keyword_floor(
            ctx,
            job_id=job_id,
            profile_version=profile_version,
            master_md=master_md,
            job_text=job_text,
        )
        raise

    score_id = None
    if job_id is not None:
        score_id = _persist_score(
            ctx,
            job_id=job_id,
            profile_version=profile_version,
            score_0_100=result.score,
            reasons=list(result.reasons),
            breakdown_md=result.breakdown_md,
            scorer_impl=SCORER_IMPL,
            feed_priority_stats=True,
        )
    return OperationOutcome(
        result_ref={"score": result.score, "job_id": job_id, "score_id": score_id},
        usage=_usage_to_dict(result.usage),
        engine=resolved.name,
        model=(_usage_to_dict(result.usage) or {}).get("model") or resolved.model,
    )


def backfill_keyword_scores(db: Database) -> int:
    """The keyword FLOOR: give every active job with NO score at the current
    profile version an instant on-device keyword score (~0.5 ms/job, no LLM),
    so no board row is ever stuck on "Pending" (maintainer 2026-07-22). Runs on
    boot, after every scan, and on switching Settings → Scoring to keyword
    mode. Jobs that already earned an AI score keep it (display precedence is
    LLM > keyword); this never deletes or overwrites anything."""
    from sidecar.modules.scorer.deterministic import score_deterministic

    from .persistence import SCORER_IMPL, SCORER_IMPL_DETERMINISTIC, compose_job_text

    with db.repos() as repos:
        profile = repos.profile.get_current()
        if profile is None:
            return 0
        master_md, version = profile.resume_markdown, int(profile.version)
        jobs = repos.jobs.list(feed_state="active")
        ids = [j.id for j in jobs]
        llm = repos.job_scores.latest_for_jobs(ids, version, scorer_impl=SCORER_IMPL)
        det = repos.job_scores.latest_for_jobs(
            ids, version, scorer_impl=SCORER_IMPL_DETERMINISTIC
        )
        todo = [
            (j.id, compose_job_text(j))
            for j in jobs
            if j.id not in llm and j.id not in det
        ]

    done = 0
    for job_id, job_text in todo:
        try:
            det_result = score_deterministic(master_md, job_text)
            with db.repos() as repos:
                repos.job_scores.upsert(
                    job_id=job_id,
                    profile_version=version,
                    score_0_100=det_result.score,
                    reasons=list(det_result.reasons),
                    breakdown_md=det_result.breakdown_md,
                    scorer_impl=SCORER_IMPL_DETERMINISTIC,
                )
            done += 1
        except Exception:  # noqa: BLE001 — one bad job never aborts the sweep
            import logging

            logging.getLogger(__name__).exception(
                "keyword-score backfill failed for job_id=%s", job_id
            )
    return done


def _persist_artifact(
    ctx: OperationContext,
    *,
    kind: str,
    markdown: str,
    notes: list[Any],
    profile_version: int,
    guidance: str,
) -> str | None:
    """Fill the pre-created `Artifact` (found by operation_id) or create one.

    Save pre-creates an empty artifact carrying `operation_id` so `packetState`
    reads *generating* while the op runs; on success we fill markdown + notes.
    A directly-enqueued op (no pre-created row) creates the artifact here."""
    if ctx.db is None:
        return None
    snap = ctx.input_snapshot
    application_id = snap.get("application_id")
    with ctx.db.repos() as repos:
        existing = (
            repos.artifacts.get_by_operation_id(ctx.operation_id)
            if ctx.operation_id is not None
            else None
        )
        if existing is not None:
            repos.artifacts.update(
                existing.id, markdown=markdown, notes=notes, profile_version=profile_version
            )
            return existing.id
        if application_id is None:
            return None
        artifact = repos.artifacts.create(
            application_id,
            kind=kind,
            markdown=markdown,
            notes=notes,
            profile_version=profile_version,
            guidance_used=guidance or None,
            operation_id=ctx.operation_id,
        )
        return artifact.id


def tailor_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Tailor a resume (Tailorer module) → its `Artifact`. Routed engine."""
    resolved = _require_engine(ctx)
    from sidecar.modules.tailorer import tailor

    from ..prompt_overrides import get_override
    from .persistence import load_job_and_master

    snap = ctx.input_snapshot
    if ctx.db is not None:
        with ctx.db.repos() as repos:
            job_text, master_md, profile_version = load_job_and_master(repos, snap)
    else:
        job_text, master_md, profile_version = snap["job"], snap["master_md"], 0

    result = tailor(
        master_md,
        job_text,
        guidance=snap.get("guidance", ""),
        engine=resolved.engine,
        skill_md=get_override("tailor"),
    )
    artifact_id = _persist_artifact(
        ctx,
        kind="tailored_resume",
        markdown=result.resume_md,
        notes=list(result.notes),
        profile_version=profile_version,
        guidance=snap.get("guidance", ""),
    )
    return OperationOutcome(
        result_ref={
            "artifact_id": artifact_id,
            "application_id": snap.get("application_id"),
            "kind": "tailored_resume",
        },
        usage=_usage_to_dict(result.usage),
        engine=resolved.name,
        model=(_usage_to_dict(result.usage) or {}).get("model") or resolved.model,
    )


def cover_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Write a cover letter (CoverLetterer module) → its `Artifact`. Routed engine."""
    resolved = _require_engine(ctx)
    from sidecar.modules.coverletterer.coverletterer import cover

    from ..prompt_overrides import get_override
    from .persistence import load_job_and_master

    snap = ctx.input_snapshot
    if ctx.db is not None:
        with ctx.db.repos() as repos:
            job_text, master_md, profile_version = load_job_and_master(repos, snap)
    else:
        job_text, master_md, profile_version = snap["job"], snap["master_md"], 0

    result = cover(
        master_md,
        job_text,
        guidance=snap.get("guidance", ""),
        engine=resolved.engine,
        skill_md=get_override("cover"),
    )
    artifact_id = _persist_artifact(
        ctx,
        kind="cover_letter",
        markdown=result.cover_letter_md,
        notes=list(result.notes),
        profile_version=profile_version,
        guidance=snap.get("guidance", ""),
    )
    return OperationOutcome(
        result_ref={
            "artifact_id": artifact_id,
            "application_id": snap.get("application_id"),
            "kind": "cover_letter",
        },
        usage=_usage_to_dict(result.usage),
        engine=resolved.name,
        model=(_usage_to_dict(result.usage) or {}).get("model") or resolved.model,
    )


def extract_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Extract the structured application profile from the current master
    resume (Profiler module) → `master_profiles.application_profile`
    (FR-APP-01). Routed engine — one small call."""
    resolved = _require_engine(ctx)
    from sidecar.modules.profiler import extract_profile

    from ..prompt_overrides import get_override

    if ctx.db is None:
        raise RuntimeError("extract operation requires a database context")
    with ctx.db.repos() as repos:
        profile_row = repos.profile.get_current()
        if profile_row is None:
            raise LookupError("no master profile to extract an application profile from")
        master_md = profile_row.resume_markdown
        version = profile_row.version

    result = extract_profile(
        master_md, engine=resolved.engine, system_prompt=get_override("extract")
    )
    record = {**result.profile, "profile_version": version, "source": "extracted"}
    with ctx.db.repos() as repos:
        repos.profile.set_application_profile(record)
    return OperationOutcome(
        result_ref={
            "profile_version": version,
            "keys_filled": sorted(k for k, v in result.profile.items() if v),
        },
        usage=_usage_to_dict(result.usage),
        engine=resolved.name,
        model=(_usage_to_dict(result.usage) or {}).get("model") or resolved.model,
    )


def default_operation_registry() -> OperationRegistry:
    """The app's real `kind → entrypoint` table. Grows as module commits land
    (architecture §5.4)."""
    # Imported here (not at module top) so the operations module stays free of
    # the networking package's playwright import cost unless a networking kind is
    # actually wired.
    from .apply_op import apply_entrypoints
    from .contact_sync_op import contact_sync_entrypoints
    from .linkedin_op import linkedin_entrypoints
    from .networker_ops import networker_entrypoints

    return OperationRegistry(
        {
            "scan": scan_entrypoint,
            "cleanup_trash": cleanup_trash_entrypoint,
            "score": score_entrypoint,
            "tailor": tailor_entrypoint,
            "cover": cover_entrypoint,
            "extract": extract_entrypoint,
            **networker_entrypoints(),  # discover / draft / send
            **linkedin_entrypoints(),  # linkedin_login / archive_stale_contacts
            **contact_sync_entrypoints(),  # contact_sync
            **apply_entrypoints(),  # apply (the Applier agent)
        }
    )
