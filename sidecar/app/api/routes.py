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
import re
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from sidecar.modules.scraper import ScraperError, probe_url
from sidecar.modules.scraper.canonical import canonicalize_url

from ..db import Database
from ..db.base import now_utc
from ..events import heartbeat_stream
from ..logging_setup import get_logger
from ..observability import reconfigure_observability
from ..observability.config import observability_config
from ..priority import STATS_KEY, zband_priority
from ..registry import EngineRegistry
from ..registry import networker_ops as networker_ops
from ..registry.company_anchor import resolution_key
from ..registry.engine_config import apply_routing
from ..registry.linkedin_op import LOGIN_CONTROL
from ..registry.networker_ops import linkedin_storage_path
from ..runner import OperationRunner
from ..scheduler.planner import plan_schedule
from . import dto
from .packet import auto_cover_default, auto_resume_default, enqueue_packet

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
    """Job ids already Saved — excluded from the board server-side (US-JB-06)."""
    return set(repos.applications.job_ids())


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


def _thread_contact_sync_cadence(repos: Any, ui_state: dict[str, Any] | None) -> None:
    """Retime the `contact_sync` schedule from `lifecycle.contact_sync_cadence_hours`
    (FR-NW-15 / FR-SYS-06). The schedule stays enabled (the entrypoint self-gates on
    the toggle + session); we only adjust its interval so the user owns the cadence.
    Clamped to ≥ 1 h so a fat-fingered 0 can't hot-loop the LinkedIn probe."""
    lifecycle = (ui_state or {}).get("lifecycle")
    if not isinstance(lifecycle, dict):
        return
    hours = lifecycle.get("contact_sync_cadence_hours")
    if not isinstance(hours, (int, float)) or hours <= 0:
        return
    minutes = int(max(1, hours) * 60)
    sched = next((s for s in repos.schedules.list_all() if s.kind == "contact_sync"), None)
    if sched is None or sched.interval_minutes == minutes:
        return
    repos.schedules.update(
        sched.id, interval_minutes=minutes,
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
            _thread_contact_sync_cadence(repos, ui_state)
        result = _settings_dto(repos)
    # Re-apply the routing map so a Settings change takes effect immediately.
    engines = _engines(request)
    if engines is not None and "engine_routing" in fields:
        apply_routing(engines, routing)
    # A6: an observability change (content logging / OTLP opt-in / retention) is
    # re-applied live — turning OTLP export ON adds the exporter, OFF removes it
    # entirely (no exporter at all — the no-network-by-default invariant).
    obs = getattr(request.app.state, "observability", None)
    if obs is not None and "ui_state" in fields:
        cfg = observability_config(ui_state)
        reconfigure_observability(
            obs,
            obs.span_db_path.parent,  # the data dir (where logfire.sqlite lives)
            content_logging=cfg.content_logging,
            otlp_enabled=cfg.otlp_enabled,
            otlp_endpoint=cfg.otlp_endpoint,
            otlp_headers=cfg.otlp_headers,
            retention_days=cfg.retention_days,
        )
    return result


@router.put("/api/settings")
async def replace_settings(
    request: Request, payload: dto.PreferencesUpdate
) -> dto.SettingsDTO:
    """PUT is an alias of POST for the settings map (idempotent update)."""
    return await update_settings(request, payload)


# -- applications (with derived packetState) --------------------------------


def _application_dto(repos: Any, application: Any) -> dto.ApplicationDTO:
    # Only head artifacts (not superseded) surface + drive packetState.
    artifacts = [
        a
        for a in repos.artifacts.list_for_application(application.id)
        if a.superseded_by is None
    ]
    with_states: list[tuple[Any, str | None]] = []
    for artifact in artifacts:
        state: str | None = None
        if artifact.operation_id is not None:
            op = repos.operations.get(artifact.operation_id)
            state = op.state if op is not None else None
        with_states.append((artifact, state))
    job = repos.jobs.get(application.job_id)
    version = _current_profile_version(repos)
    job_dto_val = None
    if job is not None:
        score = repos.job_scores.get_cached(job.id, version)
        op_states = repos.operations.score_states_by_job().get(job.id)
        job_dto_val = dto.job_dto(job, score, score_op_states=op_states)
    # Referral progress (FR-NW-01 canonical enum): landed-send count, in-flight
    # send ops, whether a discover op is running, whether a roster was found for
    # the role, and the latest reach-out batch's outcomes.
    job_id = application.job_id
    referrals_count = repos.outreach_logs.count_sent_for_job(job_id)
    send_states = [
        op.state
        for op in repos.operations.list_by_kind_states(
            "send", {"queued", "running", "failed", "succeeded"}
        )
        if (op.input_snapshot or {}).get("job_id") == job_id
    ]
    discover_in_flight = any(
        (op.input_snapshot or {}).get("job_id") == job_id
        for op in repos.operations.list_by_kind_states("discover", {"queued", "running"})
    )
    has_candidates = len(repos.contact_job_assocs.list_for_job(job_id)) > 0
    latest_batch_outcomes = [
        log.outcome for log in repos.outreach_logs.latest_batch_for_job(job_id)
    ]
    return dto.application_dto(
        application,
        with_states,
        job=job_dto_val,
        referrals_count=referrals_count,
        referrals_op_states=send_states,
        discover_in_flight=discover_in_flight,
        has_candidates=has_candidates,
        latest_batch_outcomes=latest_batch_outcomes,
        latest_apply_run=repos.apply_runs.latest_for_application(application.id),
    )


@router.get("/api/applications")
async def list_applications(
    request: Request, include_archived: bool = False
) -> list[dto.ApplicationDTO]:
    with _db(request).repos() as repos:
        apps = repos.applications.list(include_archived=include_archived)
        return [_application_dto(repos, app) for app in apps]


@router.post("/api/applications", status_code=201)
async def create_application(
    request: Request, payload: dto.ApplicationCreate
) -> dto.ApplicationDTO:
    db = _db(request)
    # 1. Create + commit the Application first (the worker must see it).
    with db.repos() as repos:
        if repos.jobs.get(payload.job_id) is None:
            raise HTTPException(status_code=404, detail=f"job {payload.job_id!r} not found")
        prefs = repos.preferences.get_or_create()
        # Priority assignment (FR-TR-09): an explicit value is a manual choice;
        # otherwise the z-band of the job's current score, or P0 if saved while
        # the score is still Pending (the strongest signal — skips the z-band).
        if payload.priority is not None:
            priority = payload.priority
        else:
            version = _current_profile_version(repos)
            score = repos.job_scores.get_cached(payload.job_id, version)
            if score is None:
                priority = "P0"
            else:
                priority = zband_priority(
                    (prefs.thresholds or {}).get(STATS_KEY), score.score_0_100
                )
        app = repos.applications.create(
            payload.job_id,
            column=payload.column,
            priority=priority,
            notes_markdown=payload.notes_markdown,
        )
        application_id = app.id
        auto_resume = auto_resume_default(prefs.thresholds)
        auto_cover = auto_cover_default(prefs.thresholds)

    # 2. Per-job automation toggles (US-TL-03): split defaults (FR-SET-02).
    # (The prior repository also enqueued Save-time form prep here; retired.)
    resume = payload.generate_resume if payload.generate_resume is not None else auto_resume
    cover = payload.generate_cover if payload.generate_cover is not None else auto_cover
    if resume or cover:
        enqueue_packet(
            db,
            _runner(request),
            application_id=application_id,
            job_id=payload.job_id,
            resume=resume,
            cover=cover,
            guidance=payload.guidance,
        )

    with db.repos() as repos:
        return _application_dto(repos, repos.applications.get(application_id))


@router.post("/api/applications/{application_id}/packet", status_code=202)
async def generate_packet(
    request: Request, application_id: str, payload: dto.PacketRequest
) -> dto.ApplicationDTO:
    """Manual/regenerate packet build (US-TL-02) — supersedes prior artifacts."""
    db = _db(request)
    with db.repos() as repos:
        app = repos.applications.get(application_id)
        if app is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        job_id = app.job_id
    enqueue_packet(
        db,
        _runner(request),
        application_id=application_id,
        job_id=job_id,
        resume=payload.resume,
        cover=payload.cover,
        guidance=payload.guidance,
    )
    with db.repos() as repos:
        return _application_dto(repos, repos.applications.get(application_id))


_ARTIFACT_KINDS = {"tailored_resume", "cover_letter"}


@router.patch("/api/applications/{application_id}/artifacts/{kind}")
async def patch_artifact(
    request: Request, application_id: str, kind: str, payload: dto.ArtifactPatch
) -> dto.ApplicationDTO:
    """Persist an edited variant + the Approve-and-Save flip (US-RES-02 / FR-RES-02).

    Targets the head (non-superseded) artifact of `kind` for this application.
    `markdown` overwrites the text (edits apply only to this variant — the master
    is untouched); `approved` stamps/clears `approved_at` (the `ready ⇄ approved`
    flip). Per-artifact, so the resume and cover letter are approved separately."""
    if kind not in _ARTIFACT_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown artifact kind {kind!r}")
    with _db(request).repos() as repos:
        app = repos.applications.get(application_id)
        if app is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        head = next(
            (
                a
                for a in repos.artifacts.list_for_application(application_id)
                if a.kind == kind and a.superseded_by is None
            ),
            None,
        )
        if head is None:
            raise HTTPException(status_code=404, detail=f"no {kind} artifact to update")
        fields: dict[str, Any] = {}
        if payload.markdown is not None:
            fields["markdown"] = payload.markdown
        if payload.approved is not None:
            fields["approved_at"] = now_utc() if payload.approved else None
        if fields:
            repos.artifacts.update(head.id, **fields)
            # Persisting a review is a touch on the card (last-touched clock).
            repos.applications.update(application_id, last_touched_at=now_utc())
        return _application_dto(repos, repos.applications.get(application_id))


@router.get("/api/applications/{application_id}")
async def get_application(request: Request, application_id: str) -> dto.ApplicationDTO:
    with _db(request).repos() as repos:
        app = repos.applications.get(application_id)
        if app is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        return _application_dto(repos, app)


_ALL_OP_STATES = {"queued", "running", "succeeded", "failed", "cancelled"}
_SCORE_LABELS = {"failed": "Score failed", "succeeded": "Scored"}


def _ops_for_job(repos: Any, kind: str, job_id: str) -> list[Any]:
    return [
        op
        for op in repos.operations.list_by_kind_states(kind, _ALL_OP_STATES)
        if (op.input_snapshot or {}).get("job_id") == job_id
    ]


def _column_label(column: str) -> str:
    """Humanize a pipeline column id for the Activity label (e.g.
    `seeking_referral` → `Seeking Referral`)."""
    return column.replace("_", " ").title()


def _event_label(kind: str, detail: dict[str, Any]) -> str:
    """The Activity-tab label for a user-driven card event (FR-TR-03/04)."""
    if kind == "column_change":
        frm = _column_label(str(detail.get("from", "")))
        to = _column_label(str(detail.get("to", "")))
        return f"Moved from {frm} to {to}"
    if kind == "notes":
        return "Notes updated"
    if kind == "archive":
        return "Archived"
    if kind == "unarchive":
        return "Restored from archive"
    return kind


@router.get("/api/applications/{application_id}/activity")
async def application_activity(
    request: Request, application_id: str
) -> list[dto.ActivityEntryDTO]:
    """Real Activity log for one application (US-TR-03 / FR-TR-03) — composed from
    the operations ledger + card events, never synthesized client-side. Records:
    added-to-tracker, score, tailor/cover generation, column moves, notes edits,
    archive/unarchive. (Apply + outreach entries return with their commits.)"""
    with _db(request).repos() as repos:
        app = repos.applications.get(application_id)
        if app is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        entries: list[dto.ActivityEntryDTO] = [
            dto.ActivityEntryDTO(kind="added", label="Added to tracker", at=app.saved_at)
        ]
        # Score ops for the job.
        for op in _ops_for_job(repos, "score", app.job_id):
            if op.state in ("succeeded", "failed"):
                entries.append(
                    dto.ActivityEntryDTO(
                        kind="score",
                        label=_SCORE_LABELS.get(op.state, "Scoring"),
                        state=op.state,
                        at=op.finished_at or op.created_at,
                    )
                )
        # Tailor / cover artifacts (head + superseded — the full generation trail).
        for artifact in repos.artifacts.list_for_application(application_id):
            op = (
                repos.operations.get(artifact.operation_id)
                if artifact.operation_id is not None
                else None
            )
            noun = "Tailored resume" if artifact.kind == "tailored_resume" else "Cover letter"
            state = op.state if op is not None else "succeeded"
            verb = "generation failed" if state == "failed" else "generated"
            entries.append(
                dto.ActivityEntryDTO(
                    kind="tailor" if artifact.kind == "tailored_resume" else "cover",
                    label=f"{noun} {verb}",
                    state=state,
                    at=(op.finished_at if op is not None else None) or artifact.created_at,
                )
            )
        # User-driven card events (FR-TR-03/04): column moves, notes edits, archive.
        for event in repos.application_events.list_for_application(application_id):
            entries.append(
                dto.ActivityEntryDTO(
                    kind=event.kind,
                    label=_event_label(event.kind, event.detail),
                    at=event.created_at,
                )
            )
    entries.sort(key=lambda e: (e.at is None, e.at))
    return entries


@router.patch("/api/applications/{application_id}")
async def update_application(
    request: Request, application_id: str, payload: dto.ApplicationUpdate
) -> dto.ApplicationDTO:
    """Move/annotate/archive a card. Column moves, notes edits, and
    archive/unarchive each write an `ApplicationEvent` (only on real change —
    a no-op PATCH records nothing). `intent` is the §5.1 exclusive value:
    setting it replaces the previous one wholesale."""
    fields = payload.model_dump(exclude_none=True)
    archived_flag = fields.pop("archived", None)
    with _db(request).repos() as repos:
        existing = repos.applications.get(application_id)
        if existing is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        events: list[tuple[str, dict[str, Any]]] = []
        if "column" in fields and fields["column"] != existing.column:
            events.append(("column_change", {"from": existing.column, "to": fields["column"]}))
        if "notes_markdown" in fields and fields["notes_markdown"] != existing.notes_markdown:
            events.append(("notes", {}))
        if archived_flag is not None:
            currently_archived = existing.archived_at is not None
            if archived_flag and not currently_archived:
                events.append(("archive", {}))
            elif not archived_flag and currently_archived:
                events.append(("unarchive", {}))
            fields["archived_at"] = now_utc() if archived_flag else None
        app = repos.applications.update(application_id, **fields)
        for kind, detail in events:
            repos.application_events.create(application_id, kind, detail)
        return _application_dto(repos, app)


@router.delete("/api/applications/{application_id}", status_code=204)
async def delete_application(request: Request, application_id: str) -> None:
    """Remove a card (unsave / return-to-board — US-JB / US-TR-07)."""
    with _db(request).repos() as repos:
        app = repos.applications.get(application_id)
        if app is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        repos.application_events.delete_for_application(application_id)
        repos.applications.delete(application_id)


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
    # A few kinds are interactive/side-effectful and must go through their own
    # dedicated route (which does the P1-consent + resource setup), never the
    # generic enqueue: `linkedin_login` opens a headed browser (use
    # `/api/linkedin/connect`). The applier's `apply`/`prep` join this list when
    # they land.
    if kind == "linkedin_login":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/linkedin/connect to start a LinkedIn login",
        )
    if kind == "apply":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/applications/{id}/apply to start an apply run",
        )
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


# -- applications: networking tab -------------------------------------------

@router.get("/api/applications/{application_id}/networking")
async def application_networking(
    request: Request, application_id: str
) -> list[dto.NetworkingContactDTO]:
    """The referral contacts linked to this role + their statuses — the detail
    modal's Networking tab (US-TR-03, shown only when LinkedIn is ON)."""
    with _db(request).repos() as repos:
        app = repos.applications.get(application_id)
        if app is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        # Latest outreach per contact for this role.
        last_by_contact: dict[str, Any] = {}
        for log in repos.outreach_logs.list_for_job(app.job_id):
            last_by_contact[log.contact_id] = log  # list is created_at-ordered
        out: list[dto.NetworkingContactDTO] = []
        for assoc in repos.contact_job_assocs.list_for_job(app.job_id):
            contact = repos.contacts.get(assoc.contact_id)
            if contact is None:
                continue
            log = last_by_contact.get(contact.id)
            out.append(
                dto.NetworkingContactDTO(
                    contact_id=contact.id,
                    name=contact.name,
                    role=contact.current_role,
                    company=contact.current_company,
                    linkedin_url=contact.linkedin_url,
                    connection_status=contact.connection_status,
                    ask_status=assoc.status,
                    audience_tag=contact.audience_tag,
                    last_message=log.body_sent if log is not None else None,
                    last_message_at=(log.sent_at or log.created_at) if log is not None else None,
                    last_outcome=log.outcome if log is not None else None,
                )
            )
    return out




# -- networking: contacts kanban (US-NW-01/02/03/07) ------------------------

def _contact_dto(repos: Any, contact: Any) -> dto.ContactDTO:
    logs = repos.outreach_logs.list_for_contact(contact.id)
    return dto.contact_dto(contact, logs[-1] if logs else None)


@router.get("/api/contacts")
async def list_contacts(
    request: Request,
    company: str | None = None,
    include_candidates: bool = False,
    archived: bool = False,
) -> list[dto.ContactDTO]:
    """The networking kanban roster (US-NW-01). Excludes archived and, by
    default, `candidate` rows (discovered-but-not-reached — off the kanban).
    `archived=true` flips it to the "Deleted Contacts" recovery view: only the
    archived rows, so a user can restore a contact they removed."""
    with _db(request).repos() as repos:
        if archived:
            rows = repos.contacts.list(company=company, include_archived=True)
            return [_contact_dto(repos, c) for c in rows if c.archived_at is not None]
        contacts = repos.contacts.list(company=company)
        if not include_candidates:
            contacts = [c for c in contacts if c.connection_status != "candidate"]
        return [_contact_dto(repos, c) for c in contacts]


@router.post("/api/contacts", status_code=201)
async def create_contact(request: Request, payload: dto.ContactCreate) -> dto.ContactDTO:
    """Manual add-a-contact by URL/name (US-NW-02) — the rank-don't-gate escape
    hatch. Always available regardless of LinkedIn state. Dedups on linkedin_url.

    Re-adding a URL that belongs to an *archived* (deleted) contact restores it
    to the kanban rather than silently returning a still-hidden row — the same
    "put it back" semantics as un-trashing a job (2026-07-10 re-add fix). Its
    prior outreach history is preserved; only the requested live column is set."""
    with _db(request).repos() as repos:
        existing = repos.contacts.get_by_url(payload.linkedin_url)
        if existing is not None:
            if existing.archived_at is not None:
                existing = repos.contacts.update(
                    existing.id,
                    archived_at=None,
                    connection_status=payload.connection_status,
                    last_touched_at=now_utc(),
                    sent_at=(
                        now_utc()
                        if payload.connection_status == "sent" and existing.sent_at is None
                        else existing.sent_at
                    ),
                )
            return _contact_dto(repos, existing)
        contact = repos.contacts.create(
            payload.linkedin_url,
            name=payload.name,
            current_company=payload.current_company,
            current_role=payload.current_role,
            connection_status=payload.connection_status,
            audience_tag=payload.audience_tag,
            sent_at=now_utc() if payload.connection_status == "sent" else None,
        )
        return _contact_dto(repos, contact)


@router.patch("/api/contacts/{contact_id}")
async def update_contact(
    request: Request, contact_id: str, payload: dto.ContactUpdate
) -> dto.ContactDTO:
    """Move a contact between kanban columns (US-NW-07) / archive / re-tag."""
    fields = payload.model_dump(exclude_none=True)
    if "archived" in fields:
        fields["archived_at"] = now_utc() if fields.pop("archived") else None
    with _db(request).repos() as repos:
        existing = repos.contacts.get(contact_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"contact {contact_id!r} not found")
        # Manual wins (US-NW-12): a user-driven column move stamps the status as
        # `manual` so the contact-status sync engine won't immediately override it.
        if "connection_status" in fields:
            fields["profile_payload"] = {
                **(existing.profile_payload or {}),
                "status_meta": {"source": "manual", "changed_at": now_utc().isoformat()},
            }
        contact = repos.contacts.update(contact_id, **fields)
        return _contact_dto(repos, contact)


# -- networking: find-referrals popup (US-NW-09 / US-REF-*) -----------------


def _require_networking_enabled(repos: Any) -> None:
    """Defense-in-depth server-side gate (audit P2-4): Referral Outreach is the
    experimental, account-risk automation — the Settings UI already gates it
    behind the toggle + ack, but a client that skips the UI (a stray call, a
    future non-web client) must not be able to trigger discovery or sends while
    the toggle is off. Mirrors `prefs.voyager_risk_marker_on`, the same flag the
    session/quota endpoints already read."""
    prefs = repos.preferences.get_or_create()
    if not bool(prefs.voyager_risk_marker_on):
        raise HTTPException(
            status_code=403,
            detail="Referral Outreach is disabled — enable it in Settings first.",
        )


@router.get("/api/jobs/{job_id}/referrals/candidates")
async def list_referral_candidates(
    request: Request, job_id: str
) -> dto.ReferralCandidatesDTO:
    """The find-referrals popup candidate list for one role (US-NW-09). Contacts
    at the job's company + per-contact template drafts + already-reached derived
    from the OutreachLog. Run discover first to populate candidates."""
    with _db(request).repos() as repos:
        job = repos.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        company = job.company
        # Roster = contacts at the target company (raw ATS name AND the resolved
        # LinkedIn entity name — they differ, e.g. `hopper` vs `Hopper`) OR already
        # linked to this job. Matching only the raw `job.company` string exact-case
        # hid discovered rosters once the `or company` mask was removed (FR-NW-02).
        company_names = {company}
        resolved = repos.company_resolutions.get(
            resolution_key(job.canonical_url, job.source_adapter, company)
        )
        if resolved is not None and resolved.company_name:
            company_names.add(resolved.company_name)
        assoc_ids = {a.contact_id for a in repos.contact_job_assocs.list_for_job(job_id)}
        contacts = repos.contacts.list_for_referrals(
            company_names=company_names, contact_ids=assoc_ids
        )
        reached_ids = {
            log.contact_id
            for log in repos.outreach_logs.list_for_job(job_id)
            if log.outcome == "sent"
        }
        # Persisted selection (FR-NW-01): restores which contacts the user picked
        # so a reopened `pending` popup shows the selection, not just the roster.
        selected_ids = repos.contact_job_assocs.selected_contact_ids(job_id)
        candidates = [
            dto.referral_candidate_dto(
                c,
                already_reached=c.id in reached_ids,
                already_selected=c.id in selected_ids,
            )
            for c in contacts
        ]
    # 1st → 2nd → 3rd degree ordering (US-NW-09 sort).
    candidates.sort(key=lambda c: (c.degree if c.degree is not None else 99))
    return dto.ReferralCandidatesDTO(
        job_id=job_id, company=company, candidates=candidates,
        already_reached_count=len(reached_ids),
    )


@router.post("/api/jobs/{job_id}/referrals/discover", status_code=202)
async def discover_referrals(
    request: Request,
    job_id: str,
    payload: dto.DiscoverReferralsRequest | None = None,
) -> dto.OperationAccepted:
    """Kick off referral discovery for a job's company (US-REF-01 / FR-NW-02).
    Enqueues a `discover` op; live progress streams as `networker` SSE events for
    the popup. `limit` is how many candidates to pull — the "find 10 more" /
    `Load more` control bumps it (10 → 20 → …) so voyager returns a larger roster
    that merges into the shared pool.

    The op first resolves the company name to a LinkedIn company entity (URN) and
    scopes the People search by it — current-employees-only, no name collisions.
    When that resolution is ambiguous the op emits a `needs_company_confirm`
    event instead of discovering; the popup then re-calls this with the user's
    chosen `company_urn` (+ name/vanity/industry), which is cached and used."""
    payload = payload or dto.DiscoverReferralsRequest()
    is_confirm = bool(payload.company_urn or payload.company_url)
    with _db(request).repos() as repos:
        _require_networking_enabled(repos)
        job = repos.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        company = job.company
        # Single-flight the plain boot discover per job (NFR-LI account safety +
        # the confirm→ask-again loop fix): if an un-confirmed discover for this job
        # is already queued/running, reuse it rather than launching a second live
        # LinkedIn scan. A confirm (URN/URL) always runs — it supersedes the boot.
        if not is_confirm:
            for op in repos.operations.list_by_kind_states("discover", {"queued", "running"}):
                snap = op.input_snapshot or {}
                if snap.get("job_id") == job_id and not (
                    snap.get("company_urn") or snap.get("company_url")
                ):
                    return dto.OperationAccepted(id=op.id, kind="discover", state=op.state)
    snapshot: dict[str, Any] = {
        "company": company, "job_id": job_id, "limit": max(1, payload.limit),
        "page": max(1, payload.page),
    }
    if payload.company_urn:
        snapshot["company_urn"] = payload.company_urn
        snapshot["company_name"] = payload.company_name or company
        snapshot["company_vanity"] = payload.company_vanity or ""
        snapshot["company_industry"] = payload.company_industry or ""
    if payload.company_url:
        snapshot["company_url"] = payload.company_url
    operation_id = _runner(request).submit("discover", snapshot)
    return dto.OperationAccepted(id=operation_id, kind="discover", state="queued")


@router.post("/api/contacts/{contact_id}/draft", status_code=202)
async def draft_referral(
    request: Request, contact_id: str, job_id: Annotated[str | None, Body(embed=True)] = None
) -> dto.OperationAccepted:
    """Grounded LLM rewrite of a contact's referral draft (US-REF-03 Regenerate).
    Enqueues a `draft` op; read the message from the operation's result_ref."""
    with _db(request).repos() as repos:
        if repos.contacts.get(contact_id) is None:
            raise HTTPException(status_code=404, detail=f"contact {contact_id!r} not found")
    operation_id = _runner(request).submit(
        "draft", {"contact_id": contact_id, "job_id": job_id}
    )
    return dto.OperationAccepted(id=operation_id, kind="draft", state="queued")


@router.post("/api/referrals/reach-out", status_code=202)
async def reach_out(request: Request, payload: dto.ReachOutRequest) -> dto.ReachOutResult:
    """Batch reach-out (US-NW-09). Enqueues one single-flight `send` op per
    selected contact — each carrying its own per-audience message. The per-action
    confirmation lives in the UI; the send path runs only when the master toggle
    is on (the UI gates it, and `_require_networking_enabled` gates it server-side
    too — audit P2-4; a dry-run plans without touching LinkedIn)."""
    runner = _runner(request)
    with _db(request).repos() as repos:
        _require_networking_enabled(repos)
        for c in payload.contacts:
            if repos.contacts.get(c.contact_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"contact {c.contact_id!r} not found"
                )
        # Persist the selection (FR-NW-01): mark every picked contact selected for
        # this role so a `pending` popup (partial / cap-stopped batch) restores who
        # was chosen on reopen. Un-picked contacts keep their prior flag — un-sent
        # picks from an earlier batch stay selected so the user can retry them.
        if payload.job_id:
            for c in payload.contacts:
                assoc = repos.contact_job_assocs.get(c.contact_id, payload.job_id)
                if assoc is not None:
                    assoc.selected = True
                else:
                    repos.contact_job_assocs.upsert(
                        c.contact_id, payload.job_id, selected=True
                    )
        # Idempotency guard (US-NW-09): a repeated "Send now" (double-click / retry)
        # must not enqueue a second real invite for a contact whose send for this
        # role is already queued or running. Skip those; the UI disables the button
        # and shows "Sending…" but this is the authoritative backstop.
        active_sends = repos.operations.list_by_kind_states("send", {"queued", "running"})
        inflight = {
            (op.input_snapshot or {}).get("contact_id")
            for op in active_sends
            if (op.input_snapshot or {}).get("job_id") == payload.job_id
        }
    # One batch id ties every send of this reach-out together, so each send's
    # entrypoint can detect *batch settle* and move the card once (FR-NW-03).
    batch_id = uuid4().hex
    enqueued: list[str] = []
    skipped: list[str] = []
    for c in payload.contacts:
        if c.contact_id in inflight:
            skipped.append(c.contact_id)
            continue
        inflight.add(c.contact_id)  # guard against duplicates within one request too
        enqueued.append(
            runner.submit("send", {
                "contact_id": c.contact_id,
                "job_id": payload.job_id,
                "application_id": payload.application_id,
                "batch_id": batch_id,
                "message": c.message,
                "dry_run": payload.dry_run,
            })
        )
    return dto.ReachOutResult(enqueued=enqueued, skipped_contact_ids=skipped)


@router.get("/api/referrals/quota")
async def referrals_quota(request: Request) -> dto.QuotaDTO:
    """Rolling outreach quota for the popup counter (US-NW-09/10). App-side view
    from the OutreachLog send windows + tier caps. The authoritative *live*
    voyager quota is the maintainer's live-dogfood path (zero traffic here)."""
    day_ago = now_utc() - timedelta(days=1)
    week_ago = now_utc() - timedelta(days=7)
    with _db(request).repos() as repos:
        session = repos.linkedin_session.get()
        prefs = repos.preferences.get_or_create()
        tier = session.account_tier if session is not None else "new"
        connected = bool(prefs.voyager_risk_marker_on) and (
            session is not None and session.status == "valid"
        )
        # Count sends across all contacts within each window. Only invites
        # (connection requests) consume the cap — 1st-degree DMs are tracked
        # separately and never decrement it (FR-NW-04 acceptance; the DM
        # counters exist so a sent DM doesn't read as "0 used").
        daily = weekly = dm_daily = dm_weekly = 0
        for contact in repos.contacts.list(include_archived=True):
            for log in repos.outreach_logs.list_for_contact(contact.id):
                if log.outcome != "sent" or log.sent_at is None:
                    continue
                is_dm = log.channel == "dm"
                if log.sent_at >= week_ago:
                    dm_weekly += is_dm
                    weekly += not is_dm
                if log.sent_at >= day_ago:
                    dm_daily += is_dm
                    daily += not is_dm
    return dto.quota_dto(
        connected=connected, tier=tier, daily_used=daily, weekly_used=weekly,
        dm_daily_sent=dm_daily, dm_weekly_sent=dm_weekly,
    )


@router.get("/api/linkedin/session")
async def linkedin_session(request: Request) -> dto.LinkedInSessionDTO:
    """LinkedIn session + master-toggle state (US-NW-09 / US-SET-06 / FR-SET-03).
    Reads the persisted session (fast — local only); the popup send path
    unlocks only when enabled AND status == 'valid'."""
    with _db(request).repos() as repos:
        session = repos.linkedin_session.get()
        prefs = repos.preferences.get_or_create()
        return dto.linkedin_session_dto(
            session, enabled=bool(prefs.voyager_risk_marker_on)
        )


@router.post("/api/linkedin/connect", status_code=202)
async def linkedin_connect(
    request: Request, payload: dto.LinkedInConnectRequest | None = None
) -> dto.OperationAccepted:
    """Start the headed-login session capture (US-SET-06 as-built). Enqueues the
    exclusive `linkedin_login` op — a visible browser opens at LinkedIn's login
    page; the user logs in themselves (the password never touches finds-you-jobs).
    `login_url` (maintainer/tests only) overrides the target with a LOCAL fixture."""
    snap: dict[str, Any] = {}
    if payload is not None and payload.login_url:
        snap["login_url"] = payload.login_url
    if payload is not None and payload.timeout_s:
        snap["timeout_s"] = payload.timeout_s
    operation_id = _runner(request).submit("linkedin_login", snap)
    return dto.OperationAccepted(id=operation_id, kind="linkedin_login", state="queued")


@router.post("/api/linkedin/cancel", status_code=202)
async def linkedin_cancel() -> dict[str, Any]:
    """Cancel an in-flight headed login (the Cancel button). Closes the browser."""
    cancelled = LOGIN_CONTROL.cancel_all()
    return {"status": "cancelling", "cancelled": cancelled}


@router.post("/api/linkedin/disconnect")
async def linkedin_disconnect(request: Request) -> dto.LinkedInSessionDTO:
    """Disconnect: cancel any in-flight login, clear the session row, and delete
    BOTH on-disk session stores — the sealed storage-state JSON and the
    persistent Chromium profile (US-SET-06 Disconnect). Before 2026-07-12 the
    profile dir survived, so a "disconnected" user's next login window opened
    already logged in. This is local deletion only — it never logs the user out
    of LinkedIn server-side (the UI says so)."""
    import shutil

    from ..registry.networker_ops import linkedin_profile_dir

    LOGIN_CONTROL.cancel_all()
    storage = linkedin_storage_path()
    try:
        storage.unlink(missing_ok=True)
    except OSError as exc:
        get_logger().warning("linkedin disconnect: could not delete session file: %s", exc)
    try:
        shutil.rmtree(linkedin_profile_dir(), ignore_errors=False)
    except FileNotFoundError:
        pass
    except OSError as exc:
        get_logger().warning("linkedin disconnect: could not delete profile dir: %s", exc)
    with _db(request).repos() as repos:
        session = repos.linkedin_session.update(
            status="never_set", connected_as="", li_at_expires_at=None,
            last_validated_at=None, paused_until=None, paused_reason="",
        )
        prefs = repos.preferences.get_or_create()
        return dto.linkedin_session_dto(session, enabled=bool(prefs.voyager_risk_marker_on))


@router.post("/api/linkedin/validate")
async def linkedin_validate(request: Request) -> dto.LinkedInSessionDTO:
    """Re-check the saved session LOCALLY (li_at presence/expiry) — **never hits
    LinkedIn** (US-SET-06 Validate). Flips status to valid / expired / never_set
    and stamps `last_validated_at`."""
    with _db(request).repos() as repos:
        session = repos.linkedin_session.get()
        tier = session.account_tier if session is not None else "new"
    driver = networker_ops.DRIVER_FACTORY(tier)
    try:
        info = driver.session_status()
    finally:
        driver.close()
    status = info.get("status", "never_set")
    with _db(request).repos() as repos:
        fields: dict[str, Any] = {"status": status, "last_validated_at": now_utc()}
        if status != "valid":
            fields["connected_as"] = ""
        session = repos.linkedin_session.update(**fields)
        prefs = repos.preferences.get_or_create()
        return dto.linkedin_session_dto(session, enabled=bool(prefs.voyager_risk_marker_on))


@router.post("/api/linkedin/resume")
async def linkedin_resume(request: Request) -> dto.LinkedInSessionDTO:
    """Clear the voyager-owned backoff pause (Settings → Networking manual resume,
    FR-NW-05 / US-REF-09). Resets the local pacing ledger and re-validates."""
    with _db(request).repos() as repos:
        session = repos.linkedin_session.get()
        tier = session.account_tier if session is not None else "new"
    driver = networker_ops.DRIVER_FACTORY(tier)
    try:
        driver.resume()
        info = driver.session_status()
    finally:
        driver.close()
    status = info.get("status", "never_set")
    with _db(request).repos() as repos:
        session = repos.linkedin_session.update(
            status=status, paused_until=None, paused_reason="",
            last_validated_at=now_utc(),
        )
        prefs = repos.preferences.get_or_create()
        return dto.linkedin_session_dto(session, enabled=bool(prefs.voyager_risk_marker_on))


@router.post("/api/linkedin/tier")
async def linkedin_set_tier(
    request: Request, payload: dto.LinkedInTierRequest
) -> dto.LinkedInSessionDTO:
    """Set the account-tier (New / Seasoned) the app passes to voyager (US-REF-08).
    voyager owns the cap *values*; this is only the user's tier selection."""
    if payload.account_tier not in ("new", "seasoned"):
        raise HTTPException(status_code=422, detail="account_tier must be 'new' or 'seasoned'")
    with _db(request).repos() as repos:
        session = repos.linkedin_session.update(account_tier=payload.account_tier)
        prefs = repos.preferences.get_or_create()
        return dto.linkedin_session_dto(session, enabled=bool(prefs.voyager_risk_marker_on))


# -- Dev tools (local testing only) ----------------------------------------
# A single-user local app on the user's own machine — these fault-injection
# endpoints power the Dev surface (US-DEV-01, dev-only): simulate an expired
# LinkedIn cookie mid-action, a crash mid-generation, and quick seed data.


@router.post("/api/dev/linkedin/expire-cookie")
async def dev_expire_linkedin_cookie(request: Request) -> dict[str, Any]:
    """Expire the `li_at` cookie in the saved session **without** touching the
    session row — so the app still believes it's connected, and the *next* real
    LinkedIn action fails on auth. Lets the maintainer test how an in-flight
    action handles a session that dies midway (graceful-failure design).

    Works whether the storage-state file is Fernet-sealed (NFR-SEC-01, 2026-07-09)
    or legacy plaintext: it unseals with the session key, sets `li_at`'s expiry to
    the past, and reseals in the SAME format. Before this fix it parsed the file
    as plaintext and silently no-op'd on any sealed session."""
    from pathlib import Path

    from ..db.database import resolve_data_dir
    from ..security import get_session_key, read_session_state, write_session_state

    data_dir = Path(getattr(request.app.state, "data_dir", None) or resolve_data_dir())
    storage = data_dir / "linkedin" / "storage_state.json"
    if not storage.exists():
        return {"ok": False, "detail": "no saved session file to expire"}
    try:
        key = get_session_key(data_dir)
    except Exception as exc:  # noqa: BLE001 — surface a missing key honestly
        raise HTTPException(
            status_code=500, detail=f"no session key available to unseal: {exc}"
        ) from exc
    try:
        state, sealed = read_session_state(storage, key)
    except Exception as exc:  # noqa: BLE001 — unreadable/undecryptable → honest 500
        raise HTTPException(
            status_code=500, detail=f"could not read session file: {exc}"
        ) from exc
    expired = 0
    for cookie in state.get("cookies", []):
        if cookie.get("name") == "li_at":
            cookie["expires"] = 1  # epoch+1s — unambiguously in the past
            expired += 1
    if expired == 0:
        return {"ok": False, "detail": "no li_at cookie in the saved session"}
    try:
        write_session_state(storage, state, key, sealed=sealed)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"could not rewrite session file: {exc}"
        ) from exc
    get_logger().info(
        "dev: expired li_at cookie (%d, sealed=%s) — session row left intact", expired, sealed
    )
    return {
        "ok": True,
        "removed_cookies": expired,
        "note": "next LinkedIn action will fail on auth",
    }


# ---------------------------------------------------------------------------
# Applier (docs/internal/applier.md §8) — direct apply runs off the Tracker
# ---------------------------------------------------------------------------


@router.post("/api/applications/{application_id}/apply", status_code=202)
async def start_apply(
    request: Request,
    application_id: str,
    payload: dto.ApplyStartRequest | None = None,
) -> dto.ApplyRunDTO:
    """Create the durable ApplyRun and enqueue the `apply` op immediately —
    no pre-Apply confirmation modal (§8.1); the click IS the action. Clicking
    Apply also settles the exclusive intent to `apply` (roadmap §5.1)."""
    payload = payload or dto.ApplyStartRequest()
    runner = _runner(request)
    with _db(request).repos() as repos:
        app_row = repos.applications.get(application_id)
        if app_row is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        job = repos.jobs.get(app_row.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job row missing")
        active = repos.apply_runs.latest_for_application(application_id)
        if active is not None and active.status in ("queued", "waiting_for_packet", "running"):
            # Single-flight per card: reopening the companion binds to the
            # active run instead of double-launching a browser (§8.2).
            return dto.apply_run_dto(active)
        if app_row.intent != "apply":
            repos.applications.update(application_id, intent="apply")
        snapshot: dict[str, Any] = {"application_id": application_id}
        if payload.retry_of_run_id:
            snapshot["retry_of_run_id"] = payload.retry_of_run_id
        if payload.dev:
            snapshot.update({f"dev_{k}": v for k, v in payload.dev.items()})
        operation_id = runner.submit("apply", snapshot)
        # Honest initial state: the run is QUEUED until the op actually starts
        # (the op flips it to waiting_for_packet/running) — the panel must not
        # claim "waiting for résumé" while the dispatcher hasn't picked it up.
        run = repos.apply_runs.create(
            application_id,
            operation_id=operation_id,
            retry_of_run_id=payload.retry_of_run_id,
            source_url=job.canonical_url,
            status="queued",
            phase="queued",
        )
        return dto.apply_run_dto(run)


@router.get("/api/applications/{application_id}/apply-runs")
async def list_apply_runs(
    request: Request, application_id: str
) -> list[dto.ApplyRunDTO]:
    with _db(request).repos() as repos:
        if repos.applications.get(application_id) is None:
            raise HTTPException(
                status_code=404, detail=f"application {application_id!r} not found"
            )
        return [
            dto.apply_run_dto(r)
            for r in repos.apply_runs.list_for_application(application_id)
        ]


@router.get("/api/apply-runs/{run_id}")
async def get_apply_run(request: Request, run_id: str) -> dto.ApplyRunDTO:
    """The run snapshot — a reopened companion fetches this instead of
    depending on having seen every prior SSE event (§9.2)."""
    with _db(request).repos() as repos:
        run = repos.apply_runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
        return dto.apply_run_dto(run)


@router.get("/api/apply-runs/{run_id}/screenshots/{index}")
async def get_apply_run_screenshot(
    request: Request, run_id: str, index: int
) -> FileResponse:
    """Serve one evidence PNG by index. Paths come from the run row only —
    never from the client — so this cannot read arbitrary files."""
    with _db(request).repos() as repos:
        run = repos.apply_runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
        shots = list(run.screenshots)
    if not (0 <= index < len(shots)):
        raise HTTPException(status_code=404, detail="no such screenshot")
    path = Path(shots[index])
    exists = await asyncio.to_thread(path.is_file)
    if not exists:
        raise HTTPException(status_code=404, detail="screenshot file missing")
    return FileResponse(path, media_type="image/png")


@router.post("/api/apply-runs/{run_id}/cancel")
async def cancel_apply_run(request: Request, run_id: str) -> dto.ApplyRunDTO:
    """Cooperative cancel (§8.2). The loop notices between steps and lands the
    run as `interrupted`; an already-terminal run is returned unchanged."""
    from ..registry.apply_op import APPLY_CONTROL

    runner = _runner(request)
    with _db(request).repos() as repos:
        run = repos.apply_runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
        if run.operation_id and run.operation_id in APPLY_CONTROL:
            # In flight: cooperative — the loop notices between steps.
            APPLY_CONTROL[run.operation_id].cancel()
        elif run.operation_id and run.status == "queued":
            # Still queued: cancel the op outright and land the run honestly.
            if runner.cancel(run.operation_id):
                run = repos.apply_runs.update(
                    run_id,
                    status="interrupted",
                    phase="interrupted",
                    summary="cancelled before the run started",
                    ended_at=now_utc(),
                )
        return dto.apply_run_dto(run)


@router.post("/api/apply-runs/{run_id}/attest")
async def attest_apply_run(
    request: Request, run_id: str, payload: dto.ApplyAttestRequest
) -> dto.ApplyRunDTO:
    """The human's word after the P1 handoff (§8.4): 'I submitted' records a
    user-attested submission and advances the card to Applied; 'didn't submit'
    leaves the card in its pre-submission column with the honest run result."""
    with _db(request).repos() as repos:
        run = repos.apply_runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
        if run.status not in ("ready_for_human", "interrupted", "timed_out", "blocked"):
            if not (run.status == "submitted" and payload.submitted):
                raise HTTPException(
                    status_code=409,
                    detail=f"run is {run.status!r}; attestation applies after the handoff",
                )
        if payload.submitted and run.status != "submitted":
            run = repos.apply_runs.update(
                run_id, status="submitted", submit_evidence="user_attested"
            )
            app_row = repos.applications.get(run.application_id)
            if app_row is not None and app_row.column in ("saved", "seeking_referral"):
                repos.applications.update(
                    run.application_id, column="applied", applied_via="applier"
                )
                repos.application_events.create(
                    run.application_id,
                    "column_change",
                    {"from": app_row.column, "to": "applied", "by": "user_attested"},
                )
        return dto.apply_run_dto(run)


# ---------------------------------------------------------------------------
# Feature-parity surfaces: prompts editor, spans drill-down, PDF export,
# browser install, dev tools (carried from the prior repository)
# ---------------------------------------------------------------------------


def _prompts_data_dir(request: Request) -> Path:
    return getattr(request.app.state, "data_dir", None) or Path()


@router.get("/api/settings/prompts")
async def list_prompts(request: Request) -> list[dto.PromptDTO]:
    """Every editable prompt with its default + current override (US-SET-12)."""
    from ..prompt_overrides import list_prompts as _list

    return [dto.PromptDTO(**row) for row in _list(_prompts_data_dir(request))]


@router.put("/api/settings/prompts/{kind}")
async def set_prompt(
    request: Request, kind: str, payload: dto.PromptUpdate
) -> dto.PromptDTO:
    """Save an override for `kind` (404 unknown kind, 422 empty markdown)."""
    from ..prompt_overrides import (
        PROMPT_KINDS,
        default_md,
        get_override,
        set_override,
    )

    spec = PROMPT_KINDS.get(kind)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown prompt kind {kind!r}")
    if not payload.markdown.strip():
        raise HTTPException(status_code=422, detail="prompt markdown cannot be empty")
    data_dir = _prompts_data_dir(request)
    set_override(kind, payload.markdown, data_dir)
    return dto.PromptDTO(
        kind=spec.kind, title=spec.title, routed=spec.routed,
        default_md=default_md(kind), override_md=get_override(kind, data_dir),
    )


@router.delete("/api/settings/prompts/{kind}")
async def reset_prompt(request: Request, kind: str) -> dto.PromptDTO:
    """Reset `kind` to its shipped default (delete the override file)."""
    from ..prompt_overrides import PROMPT_KINDS, default_md, reset

    spec = PROMPT_KINDS.get(kind)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown prompt kind {kind!r}")
    data_dir = _prompts_data_dir(request)
    reset(kind, data_dir)
    return dto.PromptDTO(
        kind=spec.kind, title=spec.title, routed=spec.routed,
        default_md=default_md(kind), override_md=None,
    )


@router.get("/api/operations/{operation_id}/spans")
async def get_operation_spans(request: Request, operation_id: str) -> list[dto.SpanDTO]:
    """The Logfire spans for one operation — the Logs drill-down (US-SYS-05 / A6).

    Reads the local `logfire.sqlite` span store (never the app schema). Returns
    an empty list when observability isn't configured or the op has no spans yet
    — the row still shows its ledger + verbatim error, so this only *enriches*."""
    from ..observability import read_spans_for_operation

    obs = getattr(request.app.state, "observability", None)
    if obs is None or getattr(obs, "span_db_path", None) is None:
        return []
    rows = await asyncio.to_thread(
        read_spans_for_operation, obs.span_db_path, operation_id
    )
    return [dto.SpanDTO(**row) for row in rows]


def downloads_dir() -> Path:
    """The user's Downloads folder (patched in tests)."""
    return Path.home() / "Downloads"


@router.post("/api/export/pdf")
async def export_pdf(payload: dto.ExportPdfRequest) -> dto.ExportPdfResult:
    """Render markdown → PDF into ~/Downloads (US-RES-03 slice, 2026-07-12).

    The webview can neither print nor download, so "Export to PDF" posts here;
    the sidecar renders with the same Chromium pipeline the Applier uploads
    (real selectable text) and saves collision-safe. Returns the saved path."""
    from ..registry.pdf import PdfRenderError, render_resume_pdf

    if not payload.markdown.strip():
        raise HTTPException(status_code=422, detail="nothing to export — the document is empty")
    stem = re.sub(r"[^\w\- ]+", "", payload.filename).strip().replace(" ", "-") or "document"
    target_dir = downloads_dir()
    await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
    path = target_dir / f"{stem}.pdf"
    n = 1
    while await asyncio.to_thread(path.exists) and n < 100:
        path = target_dir / f"{stem}-{n}.pdf"
        n += 1
    try:
        # Sync Playwright refuses to start inside a running asyncio loop (the
        # exact 503 users saw) and would block the loop anyway — render in a
        # worker thread, like the engine-verify probes.
        await asyncio.to_thread(render_resume_pdf, payload.markdown, str(path))
    except PdfRenderError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return dto.ExportPdfResult(path=str(path))


@router.post("/api/system/install-browser", status_code=202)
async def install_browser(request: Request) -> dto.BrowserInstallResult:
    """Download Playwright's Chromium (never bundled — §4.5). Coarse progress is
    published on the SSE stream as `browser_install` events. Idempotent: a second
    call while one is running returns `already_running`."""
    from .browser import start_install

    hub = getattr(request.app.state, "hub", None)
    publish = hub.publish if hub is not None else None
    status = start_install(publish)
    return dto.BrowserInstallResult(status=status)


@router.post("/api/dev/operations/fail-running")
async def dev_fail_running(request: Request) -> dict[str, Any]:
    """Mark every currently-`running` operation failed with the boot-recovery
    note — simulates the app crashing mid-generation so the Logs 'App restarted
    while generating — Retry' path (US-LOG-01) can be exercised on demand."""
    from ..runner.runner import RESTART_NOTE

    hub = getattr(request.app.state, "hub", None)
    failed: list[str] = []
    with _db(request).repos() as repos:
        for op in repos.operations.list_by_state("running"):
            repos.operations.mark_failed(op.id, error=RESTART_NOTE)
            failed.append(op.id)
            if hub is not None:
                hub.publish(
                    {"type": "operation", "payload": {"operation_id": op.id, "kind": op.kind,
                                                       "state": "failed", "error": RESTART_NOTE}}
                )
    return {"ok": True, "failed": failed, "count": len(failed)}


@router.post("/api/dev/seed-application", status_code=201)
async def dev_seed_application(request: Request) -> dict[str, Any]:
    """Create a sample Job + Saved Application so the Tracker has a card to drive
    (drag, generate, apply) without a live scrape/score. Dev-only."""
    import uuid

    suffix = uuid.uuid4().hex[:8]
    with _db(request).repos() as repos:
        job = repos.jobs.create(
            canonical_url=f"https://example.com/dev/{suffix}",
            title="Dev Sample Engineer",
            company="Devbento",
            location="Remote",
            description="A seeded job for local testing (Dev tab).",
            source_adapter="dev-seed",
        )
        app = repos.applications.create(job_id=job.id, column="saved", priority="P2")
        return {"ok": True, "job_id": job.id, "application_id": app.id}
