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

    from .persistence import persist_scan, resolve_portals, resolve_scan_prefs, scan_usage

    snap = ctx.input_snapshot
    if ctx.db is not None:
        with ctx.db.repos() as repos:
            portals = resolve_portals(snap, repos)
            prefs = resolve_scan_prefs(snap, repos=repos, portals=portals)
    else:
        portals = snap["portals_config"]
        prefs = resolve_scan_prefs(snap)
    result = scan(portals, prefs=prefs)
    result_ref = persist_scan(ctx.db, result)
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


def score_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Fit-score one job (Scorer module) → a cached `JobScore`. Routed engine."""
    resolved = _require_engine(ctx)
    from sidecar.modules.scorer.scorer import score

    from ..prompt_overrides import get_override
    from .persistence import SCORER_IMPL, load_job_and_master

    snap = ctx.input_snapshot
    job_id = snap.get("job_id")

    if ctx.db is not None:
        with ctx.db.repos() as repos:
            job_text, master_md, profile_version = load_job_and_master(repos, snap)
    else:
        job_text, master_md, profile_version = snap["job"], snap["master_md"], 0

    result = score(
        master_md, job_text, engine=resolved.engine, skill_md=get_override("score")
    )

    score_id: str | None = None
    if ctx.db is not None and job_id is not None:
        from ..priority import STATS_KEY, welford_update

        with ctx.db.repos() as repos:
            # Feed the running priority distribution (FR-TR-09) exactly once per
            # *new* score — a recompute of an existing cache key must not double
            # count (μ/σ are over jobs ever scored, not scoring attempts).
            is_new_score = (
                repos.job_scores.get_cached(job_id, profile_version, SCORER_IMPL) is None
            )
            row = repos.job_scores.upsert(
                job_id=job_id,
                profile_version=profile_version,
                score_0_100=result.score,
                reasons=list(result.reasons),
                breakdown_md=result.breakdown_md,
                scorer_impl=SCORER_IMPL,
                operation_id=ctx.operation_id,
            )
            score_id = row.id
            if is_new_score:
                prefs = repos.preferences.get_or_create()
                thresholds = dict(prefs.thresholds or {})
                thresholds[STATS_KEY] = welford_update(
                    thresholds.get(STATS_KEY), float(result.score)
                )
                repos.preferences.update(thresholds=thresholds)
    return OperationOutcome(
        result_ref={"score": result.score, "job_id": job_id, "score_id": score_id},
        usage=_usage_to_dict(result.usage),
        engine=resolved.name,
        model=(_usage_to_dict(result.usage) or {}).get("model") or resolved.model,
    )


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
