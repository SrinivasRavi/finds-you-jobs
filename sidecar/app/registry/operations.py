"""Operation registry: `kind -> entrypoint` (architecture section 5.4).

An entrypoint is the thin app-side wrapper over 1 module operation: it takes an
`OperationContext` (the input snapshot and, for LLM kinds, a resolved engine)
and returns an `OperationOutcome` (result_ref, usage, engine and model). The
runner knows only this contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING, Any

from .engines import EngineNotConfiguredError, ResolvedEngine

if TYPE_CHECKING:
    from ..db import Database, Repos

PublishFn = Callable[[dict[str, Any]], None]


@dataclass
class OperationContext:
    """Everything an entrypoint needs. `db` and `operation_id` let it persist results;
    both are None in the fake-entrypoint runner tests."""

    kind: str
    input_snapshot: dict[str, Any]
    engine: ResolvedEngine | None = None
    db: Database | None = None
    operation_id: str | None = None
    # Lets an entrypoint stream its own typed sub-events onto the SSE hub
    # (the Applier live-modal substream). None under the fake-entrypoint tests.
    publish: PublishFn | None = None
    # Cooperative cancellation (F-M7): True once the user cancelled this
    # RUNNING operation. Entrypoints poll it at safe checkpoints (the LLM
    # modules do so between retry attempts and mid-backoff) and bail by
    # raising `CompletionCancelled`. None when the runner predates the token
    # (fake-entrypoint tests) — treat as never cancelled.
    cancelled: Callable[[], bool] | None = None


@dataclass
class OperationOutcome:
    """What an entrypoint hands back for the ledger + result pointer."""

    result_ref: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    engine: str | None = None
    model: str | None = None
    # Set when the entrypoint finished and what the user asked for didn't happen:
    # the verbatim reason, recorded as a failed op. A crash still raises; this is for
    # a failure the entrypoint understands, where raising would lose the usage and
    # result pointer it must report.
    error: str | None = None


Entrypoint = Callable[[OperationContext], OperationOutcome]


class UnknownOperationKind(Exception):
    """No entrypoint registered for this kind."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"no entrypoint registered for operation kind {kind!r}")


# (snapshot, db, routed engine name) -> the service an op will call, or None
# when it calls none. The runner's circuit breaker gates and credits that service.
ServiceFn = Callable[[dict[str, Any], Any, str | None], str | None]


class OperationRegistry:
    def __init__(
        self,
        entries: dict[str, Entrypoint] | None = None,
        services: dict[str, ServiceFn] | None = None,
    ) -> None:
        self._entries: dict[str, Entrypoint] = dict(entries or {})
        self._services: dict[str, ServiceFn] = dict(services or {})

    def service_for(
        self, kind: str, snapshot: dict[str, Any], db: Any, routed: str | None
    ) -> str | None:
        """The service this op will call: the routed engine unless the kind
        says otherwise (Fast scoring calls OpenRouter whatever `score` routes to)."""
        fn = self._services.get(kind)
        return routed if fn is None else fn(snapshot, db, routed)

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


def prompt_for(ctx: OperationContext, kind: str) -> str | None:
    """The system prompt this op runs with: the saved override, or None for the
    shipped default. A prompt-tuning test carries its unsaved draft in the snapshot
    instead (url-less-jobs.md section 11.2)."""
    from ..prompt_overrides import get_override

    draft = ctx.input_snapshot.get("prompt_draft_md")
    if isinstance(draft, str) and draft.strip():
        return draft
    return get_override(kind)


def is_prompt_test(ctx: OperationContext) -> bool:
    """A test run writes nothing (no score, artifacts or profile). A snapshot flag,
    not its own kind (url-less-jobs.md P3): a test run is a tailor, so its cost
    groups and compares like one."""
    return bool(ctx.input_snapshot.get("prompt_test"))


def _load_job_inputs(
    ctx: OperationContext, *, also: Callable[[Repos], Any] | None = None
) -> tuple[str, str, int, Any]:
    """The 1 job-loading block for score, tailor and cover: `(job_text, master_md,
    profile_version)` from the DB, or the snapshot when there's none. `also` runs in
    the same repos context and returns as the 4th element."""
    from .persistence import load_job_and_master

    snap = ctx.input_snapshot
    if ctx.db is None:
        return snap["job"], snap["master_md"], 0, None
    with ctx.db.repos() as repos:
        job_text, master_md, profile_version = load_job_and_master(repos, snap)
        extra = also(repos) if also is not None else None
    return job_text, master_md, profile_version, extra


def llm_outcome(
    result_ref: dict[str, Any] | None,
    *,
    usage: Any,
    resolved: ResolvedEngine,
    model: str | None = None,
) -> OperationOutcome:
    """The 1 usage and outcome tail every routed entrypoint returns. `model`
    overrides the model precedence for `draft`, which reads it off the dataclass,
    passed through verbatim (D-A2)."""
    usage_dict = _usage_to_dict(usage)
    if model is None:
        model = (usage_dict or {}).get("model") or resolved.model
    return OperationOutcome(
        result_ref=result_ref, usage=usage_dict, engine=resolved.name, model=model
    )


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
    """The daily lifecycle tick, DB-only, windows from the lifecycle settings
    (FR-SYS-05): the Trash TTL (FR-SYS-04, 7 days), expired aging (FR-SYS-03, grey
    at 14 days, delete at 30), and the archived-application purge (FR-SYS-06, 30
    days)."""
    from ..lifecycle import LIFECYCLE_DEFAULTS, resolve_lifecycle
    from .persistence import (
        age_expired_jobs,
        backfill_fingerprints,
        backfill_resolved_locations,
        evict_stale_trash,
        purge_archived_applications,
    )

    settings = None
    if ctx.db is not None:
        with ctx.db.repos() as repos:
            settings = resolve_lifecycle(repos.preferences.get_or_create())
    settings = settings or dict(LIFECYCLE_DEFAULTS)

    tombstoned = evict_stale_trash(ctx.db, ttl_days=settings["trashed_jobs_purge_days"])
    aged = age_expired_jobs(ctx.db, expire_after_days=settings["expire_listing_days"])
    purged_apps = purge_archived_applications(
        ctx.db, retention_days=settings["archived_applications_purge_days"]
    )
    # Rows that predate the location resolver. Bounded per tick and converging:
    # a row it touches stops matching its own query.
    resolved_locations = backfill_resolved_locations(ctx.db)
    # Same shape for the duplicate check's fingerprint: bounded, converging,
    # and a no-op once every pre-migration row has one.
    fingerprinted = backfill_fingerprints(ctx.db)
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
            "resolved_locations_count": resolved_locations,
            "fingerprinted_count": fingerprinted,
        }
    )


def _wanted_locations(repos: Repos) -> Any:
    """The user's wanted locations, resolved from their preference chips.

    Read here rather than inside the scorer so the module stays free of the
    database, and resolved once per sweep rather than once per job.
    """
    from sidecar.modules.geo.resolve import resolve_wanted

    return resolve_wanted(repos.preferences.get_or_create().locations or [])


def _factor_rows(result: Any) -> list[dict[str, Any]]:
    """A scorer's typed factors in the shape the repo writes."""
    return [
        {"factor": f.factor, "value": f.value, "kind": f.kind, "evidence": f.evidence}
        for f in getattr(result, "factors", [])
    ]


def _persist_score(
    ctx: OperationContext,
    *,
    job_id: str,
    score_0_100: int,
    reasons: list[str],
    breakdown_md: str,
    scorer_impl: str,
    feed_priority_stats: bool,
    method: str | None = None,
    factors: list[dict[str, Any]] | None = None,
    profile_version: int | None = None,
) -> None:
    """Write one scorer's rating onto the job; optionally feed the running
    priority distribution (FR-TR-09) exactly once per *new* rating of that
    scorer — a recompute must not double count. A Rescore (`rescore` in the
    snapshot) may replace a real score with a Keyword one."""
    if ctx.db is None:
        return
    from ..priority import STATS_KEY, welford_update

    with ctx.db.repos() as repos:
        is_new_score = repos.jobs.set_score(
            job_id,
            scorer_impl=scorer_impl,
            score_0_100=score_0_100,
            reasons=reasons,
            breakdown_md=breakdown_md,
            method=method,
            factors=factors,
            profile_version=profile_version,
            rescore=bool(ctx.input_snapshot.get("rescore")),
        )
        if feed_priority_stats and is_new_score:
            prefs = repos.preferences.get_or_create()
            thresholds = dict(prefs.thresholds or {})
            thresholds[STATS_KEY] = welford_update(
                thresholds.get(STATS_KEY), float(score_0_100)
            )
            repos.preferences.update(thresholds=thresholds)


def _ensure_keyword_floor(
    ctx: OperationContext,
    *,
    job_id: str | None,
    master_md: str,
    job_text: str,
    profile_version: int | None = None,
) -> None:
    """Guarantee this job carries a keyword rating — idempotent: a no-op if the
    floor is already there (the usual case, since the post-scan and boot floor
    passes run first). Pure offline compute (~0.5 ms, no network). Best-effort:
    a compute error is logged, never raised — the floor is a safety net, not the
    caller's operation."""
    from sidecar.modules.scorer.deterministic import score_deterministic

    from .persistence import SCORER_IMPL_DETERMINISTIC

    if ctx.db is None or job_id is None:
        return
    with ctx.db.repos() as repos:
        job = repos.jobs.get(job_id)
        if job is None or job.score is not None:
            return
        title, location = job.title, job.location
        wanted = _wanted_locations(repos)
    try:
        det = score_deterministic(
            master_md, job_text, title=title, location=location, wanted=wanted
        )
        _persist_score(
            ctx,
            job_id=job_id,
            score_0_100=det.score,
            reasons=list(det.reasons),
            breakdown_md=det.breakdown_md,
            scorer_impl=SCORER_IMPL_DETERMINISTIC,
            method="keyword",
            factors=_factor_rows(det),
            feed_priority_stats=False,
            profile_version=profile_version,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "keyword floor failed for job_id=%s", job_id
        )


def score_service(snapshot: dict[str, Any], db: Any, routed: str | None) -> str | None:
    """Which service a `score` op calls, by the same mode rule as the entrypoint:
    Fast scoring calls OpenRouter, Keyword and On-device call nothing."""
    from .persistence import SCORING_MODES, scoring_mode

    forced = str(snapshot.get("force_mode") or "")
    if forced in SCORING_MODES:
        mode = forced
    elif db is None:
        mode = str(snapshot.get("scoring_mode") or "llm")
    else:
        with db.repos() as repos:
            mode = scoring_mode(repos.preferences.get_or_create())
    if mode == "jev":
        return "openrouter"
    return None if mode in ("keyword", "local") else routed


def score_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Fit-score 1 job. "keyword": the free on-device scorer, which is the score.
    "llm" (default): an upgrade over the keyword floor every job has; on failure the
    op fails, retryable in Logs, never auto-retried, and the board shows the floor
    (ensured here in case the floor pass hasn't run)."""
    from .persistence import (
        SCORER_IMPL,
        SCORER_IMPL_DETERMINISTIC,
        SCORING_MODES,
        scoring_mode,
    )

    snap = ctx.input_snapshot
    job_id = snap.get("job_id")

    def _scoring_context(repos: Repos) -> dict[str, Any]:
        """Mode, wanted locations and the job's own title and location, in the
        one session `_load_job_inputs` already opens."""
        from .persistence import load_decisions_client, render_preferences_block

        job = repos.jobs.get(job_id) if job_id is not None else None
        prefs = repos.preferences.get_or_create()
        # "Explain this one" (scoring-build.md T13) is the only caller that
        # names a mode: 1 click, 1 job, 1 AI call, on a board the user is
        # otherwise scoring cheaply. Everything else reads the setting, so a
        # scan can never spend on a mode the user did not pick.
        forced = str(snap.get("force_mode") or "")
        mode = forced if forced in SCORING_MODES else scoring_mode(prefs)
        return {
            "mode": mode,
            "wanted": _wanted_locations(repos),
            "preferences_md": render_preferences_block(prefs),
            "title": job.title if job is not None else "",
            "location": job.location if job is not None else "",
            # Opened for this operation only, and only for the mode that needs it.
            "decisions": load_decisions_client(repos) if mode == "jev" else None,
        }

    job_text, master_md, profile_version, scoring_ctx = _load_job_inputs(
        ctx, also=_scoring_context
    )
    # DB-free path (fake-entrypoint tests): the mode rides the snapshot instead.
    mode: str = (
        str(scoring_ctx["mode"])
        if scoring_ctx is not None
        else str(snap.get("scoring_mode") or "llm")
    )

    from sidecar.modules.scorer.deterministic import MIN_JD_CHARS, score_deterministic

    # Too thin to score, in either mode: the keyword scorer returns 0 with a
    # missing-data reason (MIN_JD_CHARS), and checking here keeps the AI path off the
    # engine, so a job with no description costs nothing. The 2 paths used to
    # disagree: under 80 chars the AI op failed, and from 80 to 199 it spent tokens on
    # an inflated score (114 and 134 jobs on the maintainer's install).
    if mode != "keyword" and len(job_text.strip()) < MIN_JD_CHARS:
        mode = "keyword"

    # "local" is on the menu because the menu never hides a method (talk.md D44),
    # and its model pack has no downloader yet (scoring-build.md T10). So it runs
    # the keyword floor and SAYS it did, in the operation's own result, rather
    # than silently scoring with something the user did not pick.
    fell_back_from: str | None = None
    if mode == "local":
        mode, fell_back_from = "keyword", "local"

    if mode == "keyword":
        det = score_deterministic(
            master_md,
            job_text,
            title=str((scoring_ctx or {}).get("title") or ""),
            location=str((scoring_ctx or {}).get("location") or ""),
            wanted=(scoring_ctx or {}).get("wanted"),
        )
        if job_id is not None:
            _persist_score(
                ctx,
                job_id=job_id,
                score_0_100=det.score,
                reasons=list(det.reasons),
                breakdown_md=det.breakdown_md,
                scorer_impl=SCORER_IMPL_DETERMINISTIC,
                method="keyword",
                factors=_factor_rows(det),
                profile_version=profile_version,
                # In keyword mode these ARE the displayed scores — they drive
                # the priority distribution. A too-thin-to-score 0 is excluded:
                # it's a missing-data marker, not a fit measurement, and feeding
                # a pile of them (248 on the maintainer's install, S-A6) would
                # drag the mean down and inflate every real job's z-band.
                feed_priority_stats=len(job_text.strip()) >= MIN_JD_CHARS,
            )
        return OperationOutcome(
            result_ref=_score_result_ref(
                det.score,
                job_id,
                "keyword",
                list(det.reasons),
                fell_back_from=fell_back_from,
            ),
            usage=None,
            engine="on-device",
            model="keyword",
        )

    if mode == "jev":
        from sidecar.modules.scorer.jev import score_jev

        client = (scoring_ctx or {}).get("decisions")
        if client is None:
            raise EngineNotConfiguredError(
                "Fast scoring needs an OpenRouter key. Add one in Settings > LLM providers, "
                "or pick another scoring method."
            )
        jev = score_jev(
            master_md,
            job_text,
            client=client,
            preferences_md=str((scoring_ctx or {}).get("preferences_md") or ""),
        )
        if job_id is not None:
            _persist_score(
                ctx,
                job_id=job_id,
                score_0_100=jev.score,
                reasons=list(jev.reasons),
                breakdown_md=jev.breakdown_md,
                scorer_impl=SCORER_IMPL,
                feed_priority_stats=True,
                method="jev",
                factors=_factor_rows(jev),
                profile_version=profile_version,
            )
        return OperationOutcome(
            result_ref=_score_result_ref(jev.score, job_id, "jev", list(jev.reasons)),
            usage=_usage_to_dict(jev.usage),
            engine="jev",
            model=jev.usage.model,
        )

    resolved = _require_engine(ctx)
    from sidecar.modules.scorer.scorer import score

    from ..runner.circuit import ProviderCircuitOpen

    try:
        result = score(
            master_md,
            job_text,
            engine=resolved.engine,
            skill_md=prompt_for(ctx, "score"),
            cancelled=ctx.cancelled,
            preferences_md=str((scoring_ctx or {}).get("preferences_md") or ""),
        )
    except Exception as exc:
        # The keyword floor is the fallback; guarantee it exists (idempotent)
        # then re-raise so the op stays failed.
        _ensure_keyword_floor(
            ctx, job_id=job_id, master_md=master_md, job_text=job_text,
            profile_version=profile_version,
        )
        # Spend an attempt only when the call reached the provider. A
        # `ProviderCircuitOpen` rejection means the runner refused to dispatch, so the job
        # returns to the pool unmarked; otherwise 1 expired key would burn every queued
        # job's budget in seconds.
        if job_id is not None and ctx.db is not None and not isinstance(
            exc, ProviderCircuitOpen
        ):
            with ctx.db.repos() as repos:
                repos.jobs.record_score_failure(job_id, f"{type(exc).__name__}: {exc}")
        raise

    if job_id is not None:
        _persist_score(
            ctx,
            job_id=job_id,
            score_0_100=result.score,
            reasons=list(result.reasons),
            breakdown_md=result.breakdown_md,
            scorer_impl=SCORER_IMPL,
            feed_priority_stats=True,
            method="llm",
            factors=_factor_rows(result),
            profile_version=profile_version,
        )
    return llm_outcome(
        _score_result_ref(result.score, job_id, "llm", list(result.reasons)),
        usage=result.usage,
        resolved=resolved,
    )


def _score_result_ref(
    score_0_100: int,
    job_id: str | None,
    method: str,
    reasons: list[Any],
    *,
    fell_back_from: str | None = None,
) -> dict[str, Any]:
    """The score op's ledger payload. `method` always: a thin JD silently runs
    keyword (url-less-jobs.md section 8.7). `fell_back_from` names the picked method
    when another ran (On-device to keyword, T10). `reasons` only on the triage path,
    where there's no job row (section 9.1.1)."""
    ref: dict[str, Any] = {"score": score_0_100, "job_id": job_id, "method": method}
    if job_id is None:
        ref["reasons"] = reasons
    if fell_back_from:
        ref["fell_back_from"] = fell_back_from
    return ref


def _keyword_sweep(db: Database, *, refresh: bool) -> int:
    """The 1 keyword sweep over the active board (about 0.5 ms per job).
    `refresh=False` is the floor: only unrated jobs get a keyword rating.
    `refresh=True` recomputes every Keyword score against the new resume; `set_score`
    never lets it replace a real score."""
    from sidecar.modules.scorer.deterministic import score_deterministic

    from .persistence import SCORER_IMPL_DETERMINISTIC, compose_job_text

    with db.repos() as repos:
        profile = repos.profile.get_current()
        if profile is None:
            return 0
        master_md, version = profile.resume_markdown, profile.version
        jobs = repos.jobs.list(feed_state="active")
        if not refresh:
            unscored = repos.jobs.ids_without_any_score([j.id for j in jobs])
            jobs = [j for j in jobs if j.id in unscored]
        wanted = _wanted_locations(repos)
        todo = [(j.id, compose_job_text(j), j.title, j.location) for j in jobs]

    done = 0
    for job_id, job_text, title, location in todo:
        try:
            det = score_deterministic(
                master_md, job_text, title=title, location=location, wanted=wanted
            )
            with db.repos() as repos:
                repos.jobs.set_score(
                    job_id,
                    scorer_impl=SCORER_IMPL_DETERMINISTIC,
                    score_0_100=det.score,
                    reasons=list(det.reasons),
                    breakdown_md=det.breakdown_md,
                    method="keyword",
                    factors=_factor_rows(det),
                    profile_version=version,
                )
            done += 1
        except Exception:  # noqa: BLE001 — one bad job never aborts the sweep
            import logging

            logging.getLogger(__name__).exception(
                "keyword sweep (refresh=%s) failed for job_id=%s", refresh, job_id
            )
    return done


def backfill_keyword_scores(db: Database) -> int:
    """The keyword FLOOR: score only never-scored jobs so no board row is ever
    stuck on "Pending". Runs on boot, after every scan, and on switching
    Settings → Scoring to keyword mode."""
    return _keyword_sweep(db, refresh=False)


def rescore_all_keyword(db: Database) -> int:
    """Fresh keyword scores at the CURRENT profile version for the whole
    board — run automatically after a resume edit in keyword mode (free)."""
    return _keyword_sweep(db, refresh=True)


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


def _resume_location(repos: Repos, job_id: Any) -> tuple[str, str, str]:
    """(mode, posting place, master's location line) for the header of a tailored
    resume and its cover letter: the 1 setting shown in Settings > Applying and
    Settings > Resume & cover letter (default the master's, D65), the job's first
    resolved place, and the location read from the master resume."""
    from sidecar.modules._shared.location_line import RESUME_LOCATION_MODES

    from .applicant import posting_place

    prefs = repos.preferences.get_or_create()
    mode = str((prefs.thresholds or {}).get("resume_location") or "master")
    if mode not in RESUME_LOCATION_MODES:
        mode = "master"
    profile = repos.profile.get_current()
    record = profile.application_profile if profile is not None else None
    master = str((record or {}).get("location") or "") if isinstance(record, dict) else ""
    job = repos.jobs.get(str(job_id)) if job_id else None
    place = posting_place(job.location or "") if job is not None else None
    return mode, place[0] if place is not None else "", master


def tailor_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Tailor a resume (Tailorer module) → its `Artifact`. Routed engine."""
    resolved = _require_engine(ctx)
    from sidecar.modules.tailorer import tailor


    snap = ctx.input_snapshot
    job_text, master_md, profile_version, location = _load_job_inputs(
        ctx, also=lambda repos: _resume_location(repos, snap.get("job_id"))
    )

    result = tailor(
        master_md,
        job_text,
        guidance=snap.get("guidance", ""),
        engine=resolved.engine,
        skill_md=prompt_for(ctx, "tailor"),
        cancelled=ctx.cancelled,
        location=location,
    )
    if is_prompt_test(ctx):
        # The output goes in the ledger row and nowhere else, so the modal can
        # read it the way every other surface reads an operation's result.
        return llm_outcome(
            {
                "prompt_test": True,
                "kind": "tailored_resume",
                "output_md": result.resume_md,
                "notes": list(result.notes),
            },
            usage=result.usage,
            resolved=resolved,
        )
    artifact_id = _persist_artifact(
        ctx,
        kind="tailored_resume",
        markdown=result.resume_md,
        notes=list(result.notes),
        profile_version=profile_version,
        guidance=snap.get("guidance", ""),
    )
    return llm_outcome(
        {
            "artifact_id": artifact_id,
            "application_id": snap.get("application_id"),
            "kind": "tailored_resume",
        },
        usage=result.usage,
        resolved=resolved,
    )


def cover_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Write a cover letter (CoverLetterer module) → its `Artifact`. Routed engine."""
    resolved = _require_engine(ctx)
    from sidecar.modules.coverletterer.coverletterer import cover


    snap = ctx.input_snapshot
    job_text, master_md, profile_version, location = _load_job_inputs(
        ctx, also=lambda repos: _resume_location(repos, snap.get("job_id"))
    )

    result = cover(
        master_md,
        job_text,
        guidance=snap.get("guidance", ""),
        engine=resolved.engine,
        skill_md=prompt_for(ctx, "cover"),
        cancelled=ctx.cancelled,
        location=location,
    )
    if is_prompt_test(ctx):
        return llm_outcome(
            {
                "prompt_test": True,
                "kind": "cover_letter",
                "output_md": result.cover_letter_md,
                "notes": list(result.notes),
            },
            usage=result.usage,
            resolved=resolved,
        )
    artifact_id = _persist_artifact(
        ctx,
        kind="cover_letter",
        markdown=result.cover_letter_md,
        notes=list(result.notes),
        profile_version=profile_version,
        guidance=snap.get("guidance", ""),
    )
    return llm_outcome(
        {
            "artifact_id": artifact_id,
            "application_id": snap.get("application_id"),
            "kind": "cover_letter",
        },
        usage=result.usage,
        resolved=resolved,
    )


def extract_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Extract the structured application profile from the current master
    resume (Profiler module) → `master_profiles.application_profile`
    (FR-APP-01). Routed engine — one small call."""
    resolved = _require_engine(ctx)
    from sidecar.modules.profiler import extract_profile


    if ctx.db is None:
        raise RuntimeError("extract operation requires a database context")
    with ctx.db.repos() as repos:
        profile_row = repos.profile.get_current()
        if profile_row is None:
            raise LookupError("no master profile to extract an application profile from")
        master_md = profile_row.resume_markdown
        version = profile_row.version

    result = extract_profile(
        master_md, engine=resolved.engine, system_prompt=prompt_for(ctx, "extract")
    )
    if is_prompt_test(ctx):
        return llm_outcome(
            {"prompt_test": True, "kind": "extract", "profile": result.profile},
            usage=result.usage,
            resolved=resolved,
        )
    from .applicant import merge_extracted

    with ctx.db.repos() as repos:
        current = repos.profile.get_current()
        record = merge_extracted(
            current.application_profile if current is not None else None,
            {**result.profile, "profile_version": version, "source": "extracted"},
        )
        repos.profile.set_application_profile(record)
    return llm_outcome(
        {
            "profile_version": version,
            "keys_filled": sorted(k for k, v in result.profile.items() if v),
        },
        usage=result.usage,
        resolved=resolved,
    )


# Kinds whose entrypoints poll `ctx.cancelled` while running, the only running
# ops a cancel can reach (F-M7). Next to the registry so a new kind faces the
# question. `apply` checks once at dispatch; its real cancel is the apply-run
# route. Queued ops of any kind stay cancellable.
CANCELLABLE_RUNNING_KINDS: frozenset[str] = frozenset({"score", "tailor", "cover"})


def default_operation_registry() -> OperationRegistry:
    """The app's real `kind → entrypoint` table. Grows as module commits land
    (architecture section 5.4)."""
    # Imported here (not at module top) so the operations module stays free of
    # the networking package's playwright import cost unless a networking kind is
    # actually wired.
    from .apply_op import apply_entrypoints
    from .contact_sync_op import contact_sync_entrypoints
    from .linkedin_op import linkedin_entrypoints
    from .networker_ops import networker_entrypoints
    from .view_page_op import view_page_entrypoints

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
            **view_page_entrypoints(),  # view_page (queued watch-surface show)
        },
        services={"score": score_service},
    )
