"""HTTP surface (architecture §4.2).

Core-storage scope (`docs/internal/roadmap.md` §7.2 #3): the lifecycle routes
(/healthz open, /shutdown bearer-guarded), the SSE `/api/events` stream fed by
the runner through the hub, and the operations API — enqueue, read, list,
retry, and the all-time cost totals. Pydantic DTOs (dto.py) are the only wire
types; ORM never crosses this line. The jobs/applications/profile/settings
CRUD lands with its feature commits.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from sidecar.modules.scraper import ScraperError, probe_url
from sidecar.modules.scraper.canonical import canonicalize_url

from ..db import Database
from ..db.base import now_utc
from ..events import heartbeat_stream
from ..logging_setup import get_logger
from ..registry import EngineRegistry
from ..registry.engine_config import apply_routing
from ..runner import OperationRunner
from ..scheduler.planner import plan_schedule
from . import dto

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# -- app.state accessors ---------------------------------------------------


def _db(request: Request) -> Database:
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="storage not initialized")
    return db


def _runner(request: Request) -> OperationRunner:
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="runner not initialized")
    return runner


# -- lifecycle -------------------------------------------------------------


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe. Open (no token) — the shell polls this (§4.4 step 2)."""
    return {"status": "ok"}


@router.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """SSE stream of typed `{type, payload}` envelopes: operation events from
    the runner, with heartbeats on idle."""
    hub = getattr(request.app.state, "hub", None)
    stream = hub.stream() if hub is not None else heartbeat_stream()
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/shutdown")
async def shutdown(request: Request) -> JSONResponse:
    """Respond 200, then exit cleanly (the drain runs in the app lifespan)."""
    get_logger().info("shutdown requested via POST /shutdown")
    request_shutdown = getattr(request.app.state, "request_shutdown", None)
    if request_shutdown is not None:
        request_shutdown()
    return JSONResponse({"status": "shutting_down"}, status_code=200)


# -- jobs ------------------------------------------------------------------


def _current_profile_version(repos: Any) -> int:
    profile = repos.profile.get_current()
    return profile.version if profile is not None else 1


def _sort_board(dtos: list[dto.JobDTO]) -> None:
    """FR-JB-01: scored feed sorted by fit (scored first, desc); unscored trail
    (Pending/Failed) by recency. In-place."""
    dtos.sort(
        key=lambda d: (d.score.score_0_100 if d.score is not None else -1, d.ingested_at),
        reverse=True,
    )


def _saved_job_ids(repos: Any) -> set[str]:
    """Job ids already Saved (excluded from the board server-side). Empty until
    the tracker commit lands `applications`."""
    applications = getattr(repos, "applications", None)
    if applications is None:
        return set()
    return set(applications.job_ids())


@router.get("/api/jobs")
async def list_jobs(request: Request, feed_state: str = "active") -> list[dto.JobDTO]:
    with _db(request).repos() as repos:
        jobs = repos.jobs.list(feed_state=feed_state or None)
        version = _current_profile_version(repos)
        scores = repos.job_scores.latest_for_jobs([j.id for j in jobs], version)
        score_op_states = repos.operations.score_states_by_job()
        dtos = [
            dto.job_dto(j, scores.get(j.id), score_op_states=score_op_states.get(j.id))
            for j in jobs
        ]
    _sort_board(dtos)
    return dtos


# Board-eligible feed states: active + expired (Expired stays on the board,
# greyed — FR-SYS-03). `removed` (Trash) and hard-deletes are off the board.
_BOARD_FEED_STATES = ["active", "expired"]
_BOARD_PAGE_SIZE = 50


def _board_search_haystack(d: dto.JobDTO, deep: bool) -> str:
    """FR-JB-13: the searchable text of one board row. Shallow (`list_q`) covers
    what the list row shows — title/company/location; deep (`text_q`) adds the
    JD body and the match-score texts (reasons + breakdown)."""
    parts = [d.title, d.company, d.location]
    if deep:
        parts += [d.salary or "", d.source_adapter, d.description]
        if d.score is not None:
            parts += [str(r) for r in d.score.reasons]
            parts.append(d.score.breakdown_md)
    return "\n".join(parts).lower()


@router.get("/api/board")
async def board(
    request: Request,
    page: int = 0,
    page_size: int = _BOARD_PAGE_SIZE,
    list_q: str = "",
    text_q: str = "",
) -> dto.BoardPageDTO:
    """The paginated Job Board feed + header meta (FR-JB-02/10). Saved jobs are
    excluded server-side; Expired jobs stay (greyed). One honest `total` count and
    a real last-scan time/status — never a silent 200-row cap or hardcoded refresh.
    `list_q` / `text_q` (FR-JB-13) filter server-side *before* pagination — the
    feed is paginated, so a client-side filter over loaded pages would silently
    miss matches on unloaded pages."""
    page = max(0, page)
    page_size = max(1, min(_BOARD_PAGE_SIZE, page_size))
    with _db(request).repos() as repos:
        saved = _saved_job_ids(repos)
        jobs = [
            j for j in repos.jobs.list_by_states(_BOARD_FEED_STATES) if j.id not in saved
        ]
        version = _current_profile_version(repos)
        scores = repos.job_scores.latest_for_jobs([j.id for j in jobs], version)
        score_op_states = repos.operations.score_states_by_job()
        dtos = [
            dto.job_dto(j, scores.get(j.id), score_op_states=score_op_states.get(j.id))
            for j in jobs
        ]
        # Scrape status/meta (FR-JB-10) — from the operations ledger, live via SSE.
        scan_running = repos.operations.any_in_flight("scan")
        last_scan = repos.operations.latest_succeeded_by_kind("scan")
        latest_scan = repos.operations.latest_by_kind("scan")
    _sort_board(dtos)
    # `empty` means the scrape found nothing — judged before search filtering,
    # so a search miss reads as a filter miss, not an empty scrape (FR-JB-13).
    feed_empty = len(dtos) == 0
    needle = list_q.strip().lower()
    if needle:
        dtos = [d for d in dtos if needle in _board_search_haystack(d, deep=False)]
    needle = text_q.strip().lower()
    if needle:
        dtos = [d for d in dtos if needle in _board_search_haystack(d, deep=True)]
    total = len(dtos)
    window = dtos[page * page_size : page * page_size + page_size]

    scan_error: str | None = None
    if scan_running:
        scan_status = "running"
    elif latest_scan is not None and latest_scan.state == "failed":
        scan_status = "error"
        scan_error = latest_scan.error
    elif feed_empty:
        scan_status = "empty"
    else:
        scan_status = "idle"
    return dto.BoardPageDTO(
        jobs=window,
        total=total,
        page=page,
        page_size=page_size,
        scan_status=scan_status,
        last_scan_at=last_scan.finished_at if last_scan is not None else None,
        scan_error=scan_error,
    )


# Honest user-facing copy for a re-add of a permanently-deleted (tombstoned)
# URL — trash is recoverable, a tombstone is final.
_TOMBSTONE_409_DETAIL = (
    "This job was permanently deleted from Trash and can't be re-added. "
    "If you still want to track it, keep a record of it outside the app."
)


@router.post("/api/jobs/preview")
async def preview_job(request: Request, payload: dto.JobPreviewRequest) -> dto.JobPreviewDTO:
    """Add-by-URL step 1 (US-JB-07): fetch the pasted URL and extract editable
    fields — best-effort, not persisted. 20 s fetch, no auto-retry (§17b). The
    blocking probe runs off the event loop.

    Two DB short-circuits before the network probe: a **tombstoned** URL fails
    fast with the honest 409 (re-add is impossible); an **existing** URL (active
    or Trashed) returns its stored fields — we "fetch it back" from our own copy
    rather than re-scrape."""
    canonical = canonicalize_url(payload.url) or payload.url
    with _db(request).repos() as repos:
        if repos.tombstones.exists(canonical):
            raise HTTPException(status_code=409, detail=_TOMBSTONE_409_DETAIL)
        existing = repos.jobs.get_by_canonical_url(canonical)
        if existing is not None:
            return dto.JobPreviewDTO(
                canonical_url=existing.canonical_url,
                title=existing.title,
                company=existing.company,
                location=existing.location,
                description=existing.description,
                posted_at=existing.posted_at or None,
                salary=existing.salary or None,
                source_adapter=existing.source_adapter or "paste-url",
            )
    try:
        job = await asyncio.to_thread(probe_url, payload.url, timeout_s=20)
    except ScraperError as e:
        # Verbatim underlying message → the modal shows it; the user can still
        # fill fields by hand (rank-don't-gate escape hatch).
        raise HTTPException(status_code=422, detail=str(e)) from e
    return dto.JobPreviewDTO(
        canonical_url=job.canonical_url,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        posted_at=job.posted_at or None,
        salary=job.salary or None,
        source_adapter=job.source_adapter or "paste-url",
    )


@router.post("/api/jobs", status_code=201)
async def create_job(request: Request, payload: dto.JobCreate) -> dto.JobDTO:
    """Add-by-URL step 2 (US-JB-07) + programmatic ingest: persist one job with
    the same dedup/tombstone discipline as scan, then enqueue a score so the new
    row lands on the board with a fit rating."""
    canonical = canonicalize_url(payload.canonical_url) or payload.canonical_url
    db = _db(request)
    enqueue_score = False
    profile_version = 1
    job_id: str | None = None
    with db.repos() as repos:
        # Tombstone = final: a permanently-deleted URL can never be re-added.
        # Trash = recoverable: a Trashed URL is restored.
        if repos.tombstones.exists(canonical):
            raise HTTPException(status_code=409, detail=_TOMBSTONE_409_DETAIL)
        existing = repos.jobs.get_by_canonical_url(canonical)
        if existing is not None:
            if existing.feed_state != "removed":
                return dto.job_dto(existing)  # already active — dedup, first-seen wins
            # Restore-from-Trash: un-trash + keep its score/history ("put it back
            # to its prior state"). Only re-score when it has no cached score —
            # so a `Score failed`/unscored job re-scores (the retry path,
            # US-JB-06), while a good score is preserved (no wasted spend).
            job = repos.jobs.set_trash_state(existing.id, trashed=False)
            version = _current_profile_version(repos)
            score = repos.job_scores.get_cached(job.id, version)
            result = dto.job_dto(job, score)
            if score is None:
                profile = repos.profile.get_current()
                if profile is not None:
                    enqueue_score = True
                    job_id, profile_version = job.id, profile.version
        else:
            job = repos.jobs.create(
                canonical_url=canonical,
                title=payload.title,
                company=payload.company,
                location=payload.location,
                description=payload.description,
                posted_at=payload.posted_at,
                salary=payload.salary,
                source_adapter=payload.source_adapter,
            )
            profile = repos.profile.get_current()
            if profile is not None:
                enqueue_score = True
                job_id, profile_version = job.id, profile.version
            result = dto.job_dto(job)
    # Score the freshly-added (or restored-but-unscored) job so it sorts into the
    # feed (US-JB-07 → FR-JB-01).
    if enqueue_score and job_id is not None:
        _runner(request).submit("score", {"job_id": job_id, "profile_version": profile_version})
    return result


@router.patch("/api/jobs/{job_id}")
async def update_job(
    request: Request, job_id: str, payload: dto.JobUpdate
) -> dto.JobDTO:
    """App-side job state (Trash — US-JB-11; Expired — FR-SYS-03). Moving into/out
    of `removed` routes through `set_trash_state` (7-day TTL clock); un-expiring an
    `expired` job routes through `unexpire` (resets the 14-day timer)."""
    fields = payload.model_dump(exclude_none=True)
    with _db(request).repos() as repos:
        current = repos.jobs.get(job_id)
        if current is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        feed_state = fields.get("feed_state")
        if feed_state == "removed":
            job = repos.jobs.set_trash_state(job_id, trashed=True)
        elif feed_state == "active" and current.feed_state == "expired":
            # Explicit un-expire (FR-SYS-03): restore + reset the 14-day timer.
            job = repos.jobs.unexpire(job_id)
        elif feed_state == "active":
            job = repos.jobs.set_trash_state(job_id, trashed=False)
        else:
            job = repos.jobs.update(job_id, **fields)
        version = _current_profile_version(repos)
        score = repos.job_scores.get_cached(job_id, version)
        op_states = repos.operations.score_states_by_job().get(job_id)
        return dto.job_dto(job, score, score_op_states=op_states)


@router.post("/api/jobs/{job_id}/tombstone")
async def tombstone_job(request: Request, job_id: str) -> dto.TombstoneResultDTO:
    """Delete forever from Trash (US-JB-11): write a `Tombstone` for the job's
    canonical URL, then hard-delete the row. A tombstone is final — a future
    scan or Add-by-URL can never re-surface it (FR-SYS-04)."""
    with _db(request).repos() as repos:
        job = repos.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        canonical = job.canonical_url
        if not repos.tombstones.exists(canonical):
            repos.tombstones.create(canonical, reason="user_delete")
        repos.jobs.delete(job_id)
    return dto.TombstoneResultDTO(tombstoned=1, canonical_urls=[canonical])


@router.post("/api/jobs/trash/empty")
async def empty_trash(request: Request) -> dto.TombstoneResultDTO:
    """Empty Trash (US-JB-11 / FR-SYS-04): tombstone every Trashed job's URL and
    hard-delete the rows immediately, bypassing the 7-day TTL."""
    urls: list[str] = []
    with _db(request).repos() as repos:
        for job in repos.jobs.list(feed_state="removed", limit=10_000):
            if not repos.tombstones.exists(job.canonical_url):
                repos.tombstones.create(job.canonical_url, reason="empty_trash")
            urls.append(job.canonical_url)
            repos.jobs.delete(job.id)
    return dto.TombstoneResultDTO(tombstoned=len(urls), canonical_urls=urls)


@router.get("/api/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dto.JobDTO:
    with _db(request).repos() as repos:
        job = repos.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        version = _current_profile_version(repos)
        score = repos.job_scores.get_cached(job_id, version)
        op_states = repos.operations.score_states_by_job().get(job_id)
        return dto.job_dto(job, score, score_op_states=op_states)


# -- profile ---------------------------------------------------------------


@router.get("/api/profile")
async def get_profile(request: Request) -> dto.ProfileDTO | None:
    with _db(request).repos() as repos:
        profile = repos.profile.get_current()
        return dto.profile_dto(profile) if profile is not None else None


@router.post("/api/profile")
async def upsert_profile(request: Request, payload: dto.ProfileUpsert) -> dto.ProfileDTO:
    with _db(request).repos() as repos:
        profile = repos.profile.upsert(payload.resume_markdown)
        result = dto.profile_dto(profile)
    # Always extract the application profile at master-save (FR-APP-01;
    # maintainer removed the toggle — it's one small cheap call and the record
    # is load-bearing for every form fill).
    _runner(request).submit("extract", {"profile_version": result.version})
    return result


@router.post("/api/profile/extract", status_code=202)
async def extract_application_profile(request: Request) -> dto.OperationAccepted:
    """Manually (re-)extract the application profile from the current master
    (the Settings "Re-extract" button — FR-APP-01)."""
    with _db(request).repos() as repos:
        if repos.profile.get_current() is None:
            raise HTTPException(status_code=404, detail="no master profile to extract from")
    operation_id = _runner(request).submit("extract", {})
    return dto.OperationAccepted(id=operation_id, kind="extract", state="queued")


@router.patch("/api/profile/application-profile")
async def patch_application_profile(
    request: Request, payload: dict[str, Any]
) -> dto.ProfileDTO:
    """Persist manual edits to the application profile (Settings editor).
    The payload replaces the stored record verbatim, stamped `source: edited`
    — deterministic user edits always win over extraction."""
    with _db(request).repos() as repos:
        if repos.profile.get_current() is None:
            raise HTTPException(status_code=404, detail="no master profile yet")
        record = {**payload, "source": "edited"}
        profile = repos.profile.set_application_profile(record)
        return dto.profile_dto(profile)


# -- settings --------------------------------------------------------------


def _settings_dto(repos: Any) -> dto.SettingsDTO:
    prefs = repos.preferences.get_or_create()
    engines = repos.engine_settings.list()
    return dto.SettingsDTO(
        preferences=dto.preferences_dto(prefs),
        engines=[dto.engine_setting_dto(e) for e in engines],
    )


@router.get("/api/settings")
async def get_settings(request: Request) -> dto.SettingsDTO:
    with _db(request).repos() as repos:
        return _settings_dto(repos)


def _engines(request: Request) -> EngineRegistry | None:
    return getattr(request.app.state, "engines", None)


# Background-scrape cadence ladder (US-OB-03 / US-SET-01) → scan-schedule
# interval. Threading happens HERE, server-side, so every writer (onboarding
# Finish, the job-finder-preferences modal, any future surface) enables the
# schedule for free — a collected cadence must actually enable the
# seeded-disabled scan schedule or a fresh install never background-scrapes.
_CADENCE_MINUTES: dict[str, int] = {
    "Every 6h": 360,
    "Every 12h": 720,
    "Every 24h": 1440,
    "Every 48h": 2880,
    "Every 72h": 4320,
}


def _thread_scan_cadence(repos: Any, ui_state: dict[str, Any] | None) -> None:
    """Enable + retime the `scan` schedule from a saved `scrape_cadence`.

    `next_due_at = now + interval` (never "now"): the writer just ran or will
    run its own immediate scan (onboarding cold-start / the modal's rescan), so
    firing the schedule immediately would double-scan. `score_new` stays
    seeded-disabled on purpose — the runner's scan→score chain already scores
    new jobs; enabling both would double-score.
    """
    cadence = (ui_state or {}).get("scrape_cadence")
    minutes = _CADENCE_MINUTES.get(cadence) if isinstance(cadence, str) else None
    if minutes is None:
        return
    sched = next((s for s in repos.schedules.list_all() if s.kind == "scan"), None)
    if sched is None:
        return
    repos.schedules.update(
        sched.id,
        enabled=True,
        interval_minutes=minutes,
        next_due_at=now_utc() + timedelta(minutes=minutes),
    )


@router.post("/api/settings")
async def update_settings(
    request: Request, payload: dto.PreferencesUpdate
) -> dto.SettingsDTO:
    # The prior repository also threads the contact-sync cadence and
    # observability reconfiguration through this write; both return with their
    # feature commits (Referral Outreach, observability).
    fields = payload.model_dump(exclude_none=True)
    with _db(request).repos() as repos:
        prefs = repos.preferences.update(**fields)
        routing = prefs.engine_routing
        ui_state = prefs.ui_state
        if "ui_state" in fields:
            _thread_scan_cadence(repos, ui_state)
        result = _settings_dto(repos)
    # Re-apply the routing map so a Settings change takes effect immediately.
    engines = _engines(request)
    if engines is not None and "engine_routing" in fields:
        apply_routing(engines, routing)
    return result


@router.put("/api/settings")
async def replace_settings(
    request: Request, payload: dto.PreferencesUpdate
) -> dto.SettingsDTO:
    """PUT is an alias of POST for the settings map (idempotent update)."""
    return await update_settings(request, payload)


# -- schedules -------------------------------------------------------------


@router.get("/api/schedules")
async def list_schedules(request: Request) -> list[dto.ScheduleDTO]:
    """The recurring-enqueue rules (scan / score_new). Seeded disabled (§7 seed)."""
    with _db(request).repos() as repos:
        return [dto.schedule_dto(s) for s in repos.schedules.list_all()]


@router.patch("/api/schedules/{schedule_id}")
async def update_schedule(
    request: Request, schedule_id: str, payload: dto.ScheduleUpdate
) -> dto.ScheduleDTO:
    """Enable/disable a schedule or change its cadence. Enabling a seeded-
    disabled schedule makes it due on the next tick (next_due_at → now)."""
    fields = payload.model_dump(exclude_none=True)
    with _db(request).repos() as repos:
        sched = repos.schedules.get(schedule_id)
        if sched is None:
            raise HTTPException(status_code=404, detail=f"schedule {schedule_id!r} not found")
        # Flip on → run promptly (the seeded next_due is far-future, §7 seed).
        if fields.get("enabled") is True and not sched.enabled:
            fields["next_due_at"] = now_utc()
        updated = repos.schedules.update(schedule_id, **fields)
        return dto.schedule_dto(updated)


@router.post("/api/schedules/{schedule_id}/run", status_code=202)
async def run_schedule(request: Request, schedule_id: str) -> dto.ScheduleRunResult:
    """Run a schedule now, regardless of enabled/due — the explicit user trigger
    (score_new fans out to a `score` op per unscored job; scan enqueues one scan).
    Idempotent for score_new: the planner skips already-scored + pending jobs."""
    db = _db(request)
    runner = _runner(request)
    with db.repos() as repos:
        sched = repos.schedules.get(schedule_id)
        if sched is None:
            raise HTTPException(status_code=404, detail=f"schedule {schedule_id!r} not found")
        kind = sched.kind
        interval_minutes = sched.interval_minutes

    planned = plan_schedule(db, kind)
    enqueued = [runner.submit(op_kind, snapshot) for op_kind, snapshot in planned]

    next_due = now_utc() + timedelta(minutes=interval_minutes)
    with db.repos() as repos:
        repos.schedules.mark_enqueued(
            schedule_id,
            operation_id=enqueued[-1] if enqueued else None,
            next_due_at=next_due,
        )
        updated = repos.schedules.get(schedule_id)
        if updated is None:  # unreachable — same txn — but keeps the type honest
            raise HTTPException(status_code=404, detail=f"schedule {schedule_id!r} not found")
        return dto.ScheduleRunResult(schedule=dto.schedule_dto(updated), enqueued=enqueued)


# -- operations ------------------------------------------------------------


@router.post("/api/operations/{kind}", status_code=202)
async def create_operation(
    request: Request,
    kind: str,
    input_snapshot: Annotated[dict[str, Any] | None, Body()] = None,
) -> dto.OperationAccepted:
    """Enqueue an operation; return its id immediately (architecture §4.2)."""
    runner = _runner(request)
    if kind not in runner.known_kinds():
        raise HTTPException(status_code=404, detail=f"unknown operation kind {kind!r}")
    operation_id = runner.submit(kind, input_snapshot or {})
    return dto.OperationAccepted(id=operation_id, kind=kind, state="queued")


@router.post("/api/operations/{operation_id}/retry", status_code=202)
async def retry_operation(request: Request, operation_id: str) -> dto.OperationAccepted:
    """Re-enqueue a failed operation with its original input snapshot — the Logs
    "App restarted while generating — retry?" affordance (US-LOG-01). Same kind,
    same inputs; a fresh operation id (the failed row stays as the audit record).
    `apply`/`linkedin_login` are excluded (interactive, non-generic paths)."""
    db = _db(request)
    with db.repos() as repos:
        op = repos.operations.get(operation_id)
        if op is None:
            raise HTTPException(status_code=404, detail=f"operation {operation_id!r} not found")
        kind, snapshot = op.kind, dict(op.input_snapshot or {})
    if kind in ("apply", "linkedin_login"):
        raise HTTPException(
            status_code=422,
            detail=f"{kind} cannot be retried from the ledger — re-run it from its own surface",
        )
    new_id = _runner(request).submit(kind, snapshot)
    # Durable old→new link (no schema change: result_ref is JSON): a FAILED row
    # that was retried renders as "Retried" instead of nagging red forever,
    # while staying in the ledger as the honest cost/audit record (US-LOG-01).
    with db.repos() as repos:
        op = repos.operations.get(operation_id)
        if op is not None and op.state == "failed":
            op.result_ref = {**(op.result_ref or {}), "retried_as": new_id}
    return dto.OperationAccepted(id=new_id, kind=kind, state="queued")


@router.get("/api/operations")
async def list_operations(request: Request, limit: int = 100) -> list[dto.OperationDTO]:
    """Recent operations — the ledger the Logs/Analytics surfaces read (§10)."""
    with _db(request).repos() as repos:
        return [dto.operation_dto(op) for op in repos.operations.list_recent(limit)]


@router.get("/api/cost/totals")
async def cost_totals(request: Request) -> dto.CostTotalsDTO:
    """All-time cost totals for the Analytics cost tiles (FR-SET-07 / US-LOG-01 #2).

    Live-ledger sum + the persisted pruned-ops aggregate, so the tiles show
    lifetime spend that survives the ~250-op ledger retention — not just the
    retained window (NFR-COST-02: the running spend total stays honest as an
    install ages)."""
    with _db(request).repos() as repos:
        return dto.cost_totals_dto(repos.all_time_cost_totals())


@router.get("/api/operations/{operation_id}")
async def get_operation(request: Request, operation_id: str) -> dto.OperationDTO:
    with _db(request).repos() as repos:
        op = repos.operations.get(operation_id)
        if op is None:
            raise HTTPException(
                status_code=404, detail=f"operation {operation_id!r} not found"
            )
        return dto.operation_dto(op)
