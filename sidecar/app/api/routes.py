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
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from sidecar.modules.scraper import ScraperError, probe_url
from sidecar.modules.scraper.canonical import canonicalize_url
from sidecar.modules.scraper.filters import passes_company, passes_content
from sidecar.modules.scraper.types import ScanPrefs

from .. import documents as docstore
from ..db.base import now_utc
from ..db.models import APPLY_RUN_ACTIVE_STATUSES, OP_ACTIVE_STATES, OP_ALL_STATES
from ..db.repos import snapshot_matches
from ..events import heartbeat_stream
from ..lifecycle import CONTACT_SYNC_MIN_INTERVAL_MINUTES
from ..logging_setup import get_logger
from ..observability import reconfigure_observability
from ..observability.config import observability_config
from ..priority import STATS_KEY, zband_priority
from ..registry import networker_ops as networker_ops
from ..registry.apply_op import (
    advance_card_to_applied,
    finalize_run_failed,
    finalize_run_interrupted,
    purge_run_dirs,
)
from ..registry.company_anchor import resolution_key
from ..registry.contact_sync_op import payload_with_status_meta
from ..registry.engine_config import apply_routing
from ..registry.linkedin_op import (
    LOGIN_CONTROL,
    SEARCH_CURSOR_EXHAUSTED,
    SEARCH_CURSOR_EXPIRED,
    SEARCH_NO_CURSOR,
    SEARCH_NOT_CONNECTED,
)
from ..registry.networker_ops import linkedin_feature_flags, linkedin_storage_path
from ..registry.persistence import (
    SCORER_IMPL,
    SCORER_IMPL_DETERMINISTIC,
    delete_application_cascade,
    scoring_mode,
)
from ..runner import OperationRunner
from ..scheduler.planner import plan_schedule
from . import dto
from .deps import db as _db
from .deps import engines as _engines
from .packet import (
    PACKET_KINDS,
    auto_cover_default,
    auto_resume_default,
    enqueue_packet,
)

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# -- app.state accessors (the shared three live in `deps.py` — D-A9) --------


def _runner(request: Request) -> OperationRunner:
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="runner not initialized")
    return runner


def _found[T](row: T | None, label: str, ident: object) -> T:
    """Get-or-404: return `row`, or raise the canonical missing-row 404.

    ONE wording for every "the id you asked for isn't there" answer (D-A5) —
    they were hand-written per site and had already drifted apart."""
    if row is None:
        raise HTTPException(status_code=404, detail=f"{label} {ident!r} not found")
    return row


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


def _scan_new_ids(last_scan: Any) -> set[str]:
    """Job ids inserted by one succeeded scan op (recorded in its result_ref) —
    the "NEW" badge set (maintainer 2026-07-23). Manual adds and rows from
    older scans are never in it; a scan recorded before `new_job_ids` existed
    yields the empty set (no badge, never an error)."""
    if last_scan is None:
        return set()
    scan_ref = (last_scan.result_ref or {}).get("scan") or {}
    return {str(job_id) for job_id in (scan_ref.get("new_job_ids") or [])}


@router.get("/api/jobs")
async def list_jobs(request: Request, feed_state: str = "active") -> list[dto.JobDTO]:
    # Bounded (200 rows), but assembled off the event loop anyway — consistent
    # with the board/applications pattern (async-first rule / F-H2).
    def _assemble() -> list[dto.JobDTO]:
        with _db(request).repos() as repos:
            jobs = repos.jobs.list(feed_state=feed_state or None)
            scores = _display_scores(repos, [j.id for j in jobs])
            score_op_states = repos.operations.score_states_by_job()
            dtos = [
                dto.job_dto(j, scores.get(j.id), score_op_states=score_op_states.get(j.id))
                for j in jobs
            ]
            new_ids = _scan_new_ids(repos.operations.latest_succeeded_by_kind("scan"))
        for d in dtos:
            d.is_new = d.id in new_ids
        _sort_board(dtos)
        return dtos

    return await asyncio.to_thread(_assemble)


def _display_scores(repos, job_ids: list[str]) -> dict:
    """The score each job DISPLAYS — the latest one (highest version; AI over
    keyword within a version), version-agnostic so a resume edit never blanks
    the board and a stale score stays visible until a re-score lands (maintainer
    2026-07-22). scorer_impl rides on the DTO so keyword scores render grey."""
    return repos.job_scores.latest_scores(job_ids)


# Board-eligible feed states: active + expired (Expired stays on the board,
# greyed — FR-SYS-03). `removed` (Trash) and hard-deletes are off the board.
_BOARD_FEED_STATES = ["active", "expired"]
_BOARD_PAGE_SIZE = 50


def _suppressed_by_excludes(state: Any, hard_excludes: dict, jobs: list[Any]) -> set[str]:
    """Job ids the user's hard excludes hide from the board — retroactively,
    so adding an exclude takes effect on ALREADY-discovered rows, not just
    future scans (maintainer 2026-07-22). Rows are hidden, never deleted:
    removing the exclude brings them straight back.

    Uses the scan chain's own matchers (`passes_company`/`passes_content`) so
    board behavior and scan behavior can't drift. Cost model (measured
    2026-07-22, ~400-word JDs): ~87 µs/job → ~0.4 s at 5 000 jobs — so the
    result is cached on app.state and recomputed only when the excludes or
    the feed change (fingerprint below). Steady-state per-request cost is a
    set lookup.
    """
    companies = [str(c) for c in (hard_excludes.get("companies") or []) if str(c).strip()]
    keywords = [str(k) for k in (hard_excludes.get("keywords") or []) if str(k).strip()]
    if not companies and not keywords:
        return set()
    fingerprint = (
        json.dumps({"c": companies, "k": keywords}, sort_keys=True),
        len(jobs),
        max((str(j.ingested_at) for j in jobs), default=""),
    )
    cached = getattr(state, "board_exclude_cache", None)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    prefs = ScanPrefs(company_block=companies, content_block=keywords)
    suppressed = {
        j.id
        for j in jobs
        if not (passes_company(j.company, prefs) and passes_content(j.title, j.description, prefs))
    }
    state.board_exclude_cache = (fingerprint, suppressed)
    return suppressed


def _board_search_haystack(job: Any, score: Any, deep: bool) -> str:
    """FR-JB-13: the searchable text of one board row. Shallow (`list_q`) covers
    what the list row shows — title/company/location; deep (`text_q`) adds the
    JD body and the match-score texts (reasons + breakdown). Reads the ORM row
    (+ its display JobScore), not the DTO — the strings are identical, and it
    lets the per-row DTO build (with its regex-derived workStyle) wait until
    after search + pagination (F-H2)."""
    parts = [job.title, job.company, job.location]
    if deep:
        parts += [job.salary or "", job.source_adapter, job.description]
        if score is not None:
            parts += [str(r) for r in score.reasons]
            parts.append(score.breakdown_md)
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
    miss matches on unloaded pages.

    The whole assembly runs off the event loop (async-first rule / F-H2 — at a
    few thousand jobs it could hold the loop past the shell's 2 s health window),
    and the DTO build (three regexes over the full JD each) happens only for the
    returned page, not every eligible row."""
    page = max(0, page)
    page_size = max(1, min(_BOARD_PAGE_SIZE, page_size))

    def _assemble() -> dto.BoardPageDTO:
        with _db(request).repos() as repos:
            saved = _saved_job_ids(repos)
            jobs = [
                j
                for j in repos.jobs.list_by_states(_BOARD_FEED_STATES)
                if j.id not in saved
            ]
            # Personal hard excludes apply to already-discovered rows too —
            # hidden (cached set), never deleted (maintainer 2026-07-22).
            excludes = repos.preferences.get_or_create().hard_excludes or {}
            suppressed = _suppressed_by_excludes(request.app.state, excludes, jobs)
            if suppressed:
                jobs = [j for j in jobs if j.id not in suppressed]
            scores = _display_scores(repos, [j.id for j in jobs])
            score_op_states = repos.operations.score_states_by_job()
            # Scrape status/meta (FR-JB-10) — from the operations ledger, live via SSE.
            scan_running = repos.operations.any_in_flight("scan")
            last_scan = repos.operations.latest_succeeded_by_kind("scan")
            latest_scan = repos.operations.latest_by_kind("scan")
            new_ids = _scan_new_ids(last_scan)
        # Sort + search over (job, score) rows — same key/haystack as the DTO
        # path (FR-JB-01 / FR-JB-13); the DTO is built only for the window below.
        rows = [(j, scores.get(j.id)) for j in jobs]
        rows.sort(
            key=lambda r: (
                r[1].score_0_100 if r[1] is not None else -1,
                r[0].ingested_at,
            ),
            reverse=True,
        )
        # `empty` means the scrape found nothing — judged before search filtering,
        # so a search miss reads as a filter miss, not an empty scrape (FR-JB-13).
        feed_empty = len(rows) == 0
        needle = list_q.strip().lower()
        if needle:
            rows = [r for r in rows if needle in _board_search_haystack(*r, deep=False)]
        needle = text_q.strip().lower()
        if needle:
            rows = [r for r in rows if needle in _board_search_haystack(*r, deep=True)]
        total = len(rows)
        window = [
            dto.job_dto(j, s, score_op_states=score_op_states.get(j.id))
            for j, s in rows[page * page_size : page * page_size + page_size]
        ]
        for d in window:
            d.is_new = d.id in new_ids

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

    return await asyncio.to_thread(_assemble)


@router.get("/api/scan/progress")
async def scan_progress(request: Request) -> dto.ScanProgressDTO:
    """Board-level scan + scoring progress (observed-issue #2): a small,
    schema-free read the board polls to show "scanning…" and "M of N scored".

    Assembled entirely from the operations ledger (no new persisted state):
    `scan_running` = a scan op is in flight; `last_scan_at` = the latest
    succeeded scan's finish time; `new_found` = that scan's recorded
    `new_job_ids` count; and the scoring split — `score_pending` is the live
    (queued/running) score-op count, `score_done` is how many of THIS scan's
    new jobs have reached a terminal (succeeded/failed) score. Off the event
    loop (async-first rule), one session inside the callable."""

    def _assemble() -> dto.ScanProgressDTO:
        with _db(request).repos() as repos:
            scan_running = repos.operations.any_in_flight("scan")
            last_scan = repos.operations.latest_succeeded_by_kind("scan")
            new_ids = _scan_new_ids(last_scan)
            score_pending = len(
                repos.operations.list_by_kind_states("score", OP_ACTIVE_STATES)
            )
            # Batch-scoped "done": score ops for THIS scan's new jobs that have
            # reached a terminal state and are not currently re-pending. The
            # job_id lives in each score op's input_snapshot; score_states_by_job
            # maps it to that job's set of score-op states in one pass.
            score_states = repos.operations.score_states_by_job()
            score_done = 0
            for job_id in new_ids:
                states = score_states.get(job_id, set())
                if states & OP_ACTIVE_STATES:
                    continue
                if states & {"succeeded", "failed"}:
                    score_done += 1
            return dto.ScanProgressDTO(
                scan_running=scan_running,
                last_scan_at=last_scan.finished_at if last_scan is not None else None,
                new_found=len(new_ids),
                score_pending=score_pending,
                score_done=score_done,
            )

    return await asyncio.to_thread(_assemble)


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
            # to its prior state"). Re-score only when the CURRENT mode has no
            # score at the current version — an AI-mode job carrying just the
            # grey keyword floor re-enqueues its AI upgrade (the retry path,
            # US-JB-06), while a good score of the active mode is preserved
            # (no wasted spend).
            job = repos.jobs.set_trash_state(existing.id, trashed=False)
            version = _current_profile_version(repos)
            mode = scoring_mode(repos.preferences.get_or_create())
            impl = SCORER_IMPL if mode == "llm" else SCORER_IMPL_DETERMINISTIC
            score = repos.job_scores.get_cached(job.id, version, impl)
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
        current = _found(repos.jobs.get(job_id), "job", job_id)
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
        job = _found(repos.jobs.get(job_id), "job", job_id)
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
        job = _found(repos.jobs.get(job_id), "job", job_id)
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
        before = repos.profile.get_current()
        changed = before is None or before.resume_markdown != payload.resume_markdown
        profile = repos.profile.upsert(payload.resume_markdown)
        result = dto.profile_dto(profile)
        mode = scoring_mode(repos.preferences.get_or_create())
    # Always extract the application profile at master-save (FR-APP-01;
    # maintainer removed the toggle — it's one small cheap call and the record
    # is load-bearing for every form fill).
    _runner(request).submit("extract", {"profile_version": result.version})
    # Re-scoring on a resume edit (maintainer 2026-07-23): keyword mode is free,
    # so re-score the whole board now (off the event loop). AI mode costs
    # tokens, so it does NOT auto-run — the frontend previews the cache misses
    # and calls /api/jobs/rescore only if the user confirms; otherwise the
    # prior scores stay visible (the board shows the latest available version).
    # An unchanged save keeps its version (no bump), so nothing is stale and
    # nothing re-scores.
    if changed and mode == "keyword":
        from ..registry.operations import rescore_all_keyword

        await asyncio.to_thread(rescore_all_keyword, _db(request))
    return result


def _rescore_missing(repos: Any) -> tuple[list[str], int, int]:
    """The AI re-score candidate set: active jobs MISSING an AI score at the
    current profile version (cache misses), plus the active total and the
    version. ONE helper for the preview and the run, so the prompt's N always
    equals what a confirmed run enqueues."""
    version = _current_profile_version(repos)
    job_ids = [j.id for j in repos.jobs.list(feed_state="active")]
    missing = repos.job_scores.job_ids_missing_llm_score(job_ids, version)
    return missing, len(job_ids), version


@router.get("/api/jobs/rescore/preview")
async def rescore_preview(request: Request) -> dto.RescorePreviewDTO:
    """The consent numbers behind every "Re-score with AI?" prompt (resume
    edit, scoring-mode switch). Counts only — never enqueues, never spends.
    A grey keyword score is not "cached" here; only a real AI score at the
    current resume version is."""
    with _db(request).repos() as repos:
        missing, total, _version = _rescore_missing(repos)
    return dto.RescorePreviewDTO(to_score=len(missing), cached=total - len(missing))


@router.post("/api/jobs/rescore")
async def rescore_board(request: Request) -> dict[str, int]:
    """Re-score the active board against the CURRENT master resume — the action
    behind every "Re-score with AI?" confirm (resume edit, scoring-mode
    switch). Keyword mode refreshes the whole board inline (free). AI mode
    enqueues one LLM score op per cache MISS only — a job already AI-scored at
    the current resume version is never re-spent (maintainer 2026-07-23) — so
    the call is idempotent and safe from any entry point."""
    with _db(request).repos() as repos:
        mode = scoring_mode(repos.preferences.get_or_create())
        missing, total, version = _rescore_missing(repos)
    if mode == "keyword":
        from ..registry.operations import rescore_all_keyword

        n = await asyncio.to_thread(rescore_all_keyword, _db(request))
        return {"rescored": n}
    runner = _runner(request)
    for job_id in missing:
        runner.submit("score", {"job_id": job_id, "profile_version": version})
    return {"queued": len(missing), "skipped": total - len(missing)}


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
    from ..registry.persistence import SCRAPER_ENGINE_PREFIX

    prefs = repos.preferences.get_or_create()
    engines = repos.engine_settings.list()
    return dto.SettingsDTO(
        preferences=dto.preferences_dto(prefs),
        # `scraper:*` rows are BYO scraper keys (Apify/Brave) riding the same
        # sealed store — never LLM engines; they surface via /api/discovery.
        engines=[
            dto.engine_setting_dto(e)
            for e in engines
            if not e.engine.startswith(SCRAPER_ENGINE_PREFIX)
        ],
    )


@router.get("/api/settings")
async def get_settings(request: Request) -> dto.SettingsDTO:
    with _db(request).repos() as repos:
        return _settings_dto(repos)


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
    fields = payload.model_dump(exclude_none=True)
    with _db(request).repos() as repos:
        before_mode = scoring_mode(repos.preferences.get_or_create())
        prefs = repos.preferences.update(**fields)
        routing = prefs.engine_routing
        ui_state = prefs.ui_state
        prefs_thresholds = dict(prefs.thresholds or {})
        if "ui_state" in fields:
            _thread_scan_cadence(repos, ui_state)
        result = _settings_dto(repos)
    # Switching Scoring to keyword mode scores the whole board right here —
    # ~0.5 ms/job, no LLM — so the change is visible on the next board fetch
    # instead of waiting for a scan. Off the event loop (async-first rule).
    # Switching to AI mode enqueues NOTHING server-side: the frontend fetches
    # /api/jobs/rescore/preview and asks before any token is spent.
    after_mode = str(prefs_thresholds.get("scoring_mode") or "llm")
    if after_mode == "keyword" and before_mode != "keyword":
        from ..registry.operations import backfill_keyword_scores

        await asyncio.to_thread(backfill_keyword_scores, _db(request))
    # Re-apply the routing map so a Settings change takes effect immediately.
    engines = _engines(request)
    if engines is not None and "engine_routing" in fields:
        apply_routing(engines, routing)
    # LLM parallelism is re-capped live (2026-07-17): the runner swaps its
    # policy on the next pump; running ops are never interrupted.
    if "thresholds" in fields:
        from ..runner.policy import llm_concurrency_from

        _runner(request).set_llm_limit(llm_concurrency_from(prefs_thresholds))
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


def _application_dtos(repos: Any, applications: list[Any]) -> list[dto.ApplicationDTO]:
    """Assemble ApplicationDTOs for a batch of cards (F-H2): the card-invariant
    lookups (profile version, score-op scan, send/discover op lists) run ONCE
    per call, and every child table is fetched with one IN(ids) query — so the
    tracker list at N cards issues a constant number of queries, never ~12+ per
    card."""
    if not applications:
        return []
    app_ids = [a.id for a in applications]
    job_ids = list({a.job_id for a in applications})
    # Card-invariant lookups — hoisted out of the per-card loop.
    version = _current_profile_version(repos)
    score_op_states = repos.operations.score_states_by_job()
    send_ops = repos.operations.list_by_kind_states(
        "send", {"queued", "running", "failed", "succeeded"}
    )
    discover_ops = repos.operations.list_by_kind_states(
        "discover", OP_ACTIVE_STATES
    )
    # Batched child rows — one IN query per table.
    # Only head artifacts (not superseded) surface + drive packetState.
    artifacts_by_app: dict[str, list[Any]] = {}
    for artifact in repos.artifacts.list_for_applications(app_ids):
        if artifact.superseded_by is None:
            artifacts_by_app.setdefault(artifact.application_id, []).append(artifact)
    ops_by_id = repos.operations.get_many(
        [
            a.operation_id
            for artifacts in artifacts_by_app.values()
            for a in artifacts
            if a.operation_id is not None
        ]
    )
    jobs_by_id = repos.jobs.get_many(job_ids)
    scores = repos.job_scores.latest_for_jobs(job_ids, version)
    sent_counts = repos.outreach_logs.count_sent_for_jobs(job_ids)
    latest_batches = repos.outreach_logs.latest_batches_for_jobs(job_ids)
    jobs_with_candidates = repos.contact_job_assocs.job_ids_with_contacts(job_ids)
    links_by_app: dict[str, list[Any]] = {}
    for link in repos.application_documents.list_for_applications(app_ids):
        links_by_app.setdefault(link.application_id, []).append(link)
    docs_by_id = repos.documents.get_many(
        [link.document_id for links in links_by_app.values() for link in links]
    )
    latest_runs = repos.apply_runs.latest_for_applications(app_ids)

    results: list[dto.ApplicationDTO] = []
    for application in applications:
        job_id = application.job_id
        with_states: list[tuple[Any, str | None]] = []
        for artifact in artifacts_by_app.get(application.id, []):
            state: str | None = None
            if artifact.operation_id is not None:
                op = ops_by_id.get(artifact.operation_id)
                state = op.state if op is not None else None
            with_states.append((artifact, state))
        job = jobs_by_id.get(job_id)
        job_dto_val = None
        if job is not None:
            job_dto_val = dto.job_dto(
                job, scores.get(job_id), score_op_states=score_op_states.get(job_id)
            )
        # Referral progress (FR-NW-01 canonical enum): landed-send count, in-flight
        # send ops, whether a discover op is running, whether a roster was found for
        # the role, and the latest reach-out batch's outcomes.
        send_states = [
            op.state for op in send_ops if snapshot_matches(op, "job_id", job_id)
        ]
        discover_in_flight = any(
            snapshot_matches(op, "job_id", job_id) for op in discover_ops
        )
        # Attached documents (manual cards) — the resume/cover the user submitted.
        attached_docs = [
            (link, docs_by_id[link.document_id])
            for link in links_by_app.get(application.id, [])
            if link.document_id in docs_by_id
        ]
        results.append(
            dto.application_dto(
                application,
                with_states,
                job=job_dto_val,
                referrals_count=sent_counts.get(job_id, 0),
                referrals_op_states=send_states,
                discover_in_flight=discover_in_flight,
                has_candidates=job_id in jobs_with_candidates,
                latest_batch_outcomes=[
                    log.outcome for log in latest_batches.get(job_id, [])
                ],
                latest_apply_run=latest_runs.get(application.id),
                documents=attached_docs,
            )
        )
    return results


def _application_dto(repos: Any, application: Any) -> dto.ApplicationDTO:
    return _application_dtos(repos, [application])[0]


@router.get("/api/applications")
async def list_applications(
    request: Request, include_archived: bool = False
) -> list[dto.ApplicationDTO]:
    # Assembly runs off the event loop (async-first rule / F-H2): the session is
    # created AND used entirely inside the worker thread, never shared with it.
    def _assemble() -> list[dto.ApplicationDTO]:
        with _db(request).repos() as repos:
            apps = repos.applications.list(include_archived=include_archived)
            return _application_dtos(repos, apps)

    return await asyncio.to_thread(_assemble)


@router.post("/api/applications", status_code=201)
async def create_application(
    request: Request, payload: dto.ApplicationCreate
) -> dto.ApplicationDTO:
    db = _db(request)
    # 1. Create + commit the Application first (the worker must see it).
    with db.repos() as repos:
        _found(repos.jobs.get(payload.job_id), "job", payload.job_id)
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


# Manual application `column` values shown on the Tracker card menu — the
# post-referral pipeline stages a real-world "already applied" record can land in.
_MANUAL_COLUMNS = {"applied", "interviewing", "offer", "rejected"}


@router.post("/api/applications/manual", status_code=201)
async def create_manual_application(
    request: Request,
    canonical_url: Annotated[str, Form()],
    title: Annotated[str, Form()] = "",
    company: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    posted_at: Annotated[str, Form()] = "",
    salary: Annotated[str, Form()] = "",
    source_adapter: Annotated[str, Form()] = "paste-url",
    column: Annotated[str, Form()] = "applied",
    notes_markdown: Annotated[str, Form()] = "",
    resume: Annotated[UploadFile | None, File()] = None,
    cover: Annotated[UploadFile | None, File()] = None,
) -> dto.ApplicationDTO:
    """Log a job the user already applied to OUTSIDE the app ("Add a job
    application" — the Tracker sibling of the board's Add-by-URL). Upserts the
    job with the same dedup/tombstone discipline, creates the card as
    `origin=manual` in a post-referral stage (default Applied), and attaches the
    resume/cover the user actually submitted (optional) as content-addressed,
    deduped documents. No score is enqueued — they already applied, so fit
    ranking is moot."""
    db = _db(request)
    canonical = canonicalize_url(canonical_url) or canonical_url
    stage = column or "applied"
    if stage not in _MANUAL_COLUMNS:
        raise HTTPException(
            status_code=422,
            detail=f"stage must be one of {sorted(_MANUAL_COLUMNS)}",
        )

    # 1. Read + validate any uploads up front (fail fast before we create rows).
    prepared: list[tuple[str, bytes, str]] = []  # (kind, data, filename)
    for kind, upload in (("tailored_resume", resume), ("cover_letter", cover)):
        if upload is None or not upload.filename:
            continue
        data = await upload.read()
        try:
            docstore.validate(upload.filename, data)
        except (docstore.DocumentTooLarge, docstore.UnsupportedDocumentType) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        prepared.append((kind, data, upload.filename))

    # 2. Upsert the job + create the manual card (refusing to double-track).
    with db.repos() as repos:
        if repos.tombstones.exists(canonical):
            raise HTTPException(status_code=409, detail=_TOMBSTONE_409_DETAIL)
        existing_job = repos.jobs.get_by_canonical_url(canonical)
        if existing_job is not None:
            job = existing_job
            if job.feed_state == "removed":
                job = repos.jobs.set_trash_state(job.id, trashed=False)
            if job.id in repos.applications.job_ids():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This job is already in your tracker — move that card to "
                        "Applied instead of adding it again."
                    ),
                )
        else:
            job = repos.jobs.create(
                canonical_url=canonical,
                title=title or canonical,
                company=company,
                location=location,
                description=description,
                posted_at=posted_at or None,
                salary=salary or None,
                source_adapter=source_adapter or "paste-url",
            )
        app = repos.applications.create(
            job.id,
            column=stage,
            origin="manual",
            applied_via="manual",
            notes_markdown=notes_markdown,
        )
        application_id = app.id

    # 3. Store uploads off the event loop (deduped on disk), then index + link
    #    them — only now that the card exists (no orphan blobs on the 409 path).
    stored: list[tuple[str, str, int, str, str]] = []  # kind, sha, size, mime, name
    for kind, data, filename in prepared:
        sha = await asyncio.to_thread(docstore.store_bytes, data, db.data_dir)
        stored.append(
            (kind, sha, len(data), docstore.mime_for_filename(filename), filename)
        )
    if stored:
        with db.repos() as repos:
            for kind, sha, size, mime, filename in stored:
                doc = repos.documents.get_or_create(
                    sha256=sha,
                    byte_size=size,
                    mime_type=mime,
                    original_filename=filename,
                )
                repos.application_documents.set(application_id, kind, doc.id)

    with db.repos() as repos:
        return _application_dto(repos, repos.applications.get(application_id))


@router.post("/api/applications/{application_id}/documents", status_code=201)
async def attach_application_document(
    request: Request,
    application_id: str,
    kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> dto.ApplicationDTO:
    """Attach a resume/cover FILE to an EXISTING application — the Upload button
    on the Tailored resume / Cover letter editors. Stores it content-addressed
    (deduped) and links it as the application's `kind` slot, replacing any prior
    file for that kind (one resume + one cover per card). This is the exact
    document the user submits on Apply / the record for a manual card logged
    without a file. `kind` ∈ {'tailored_resume', 'cover_letter'}."""
    if kind not in PACKET_KINDS:
        raise HTTPException(
            status_code=422, detail=f"kind must be one of {list(PACKET_KINDS)}"
        )
    db = _db(request)
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=422, detail="No file was uploaded.")
    data = await file.read()
    try:
        docstore.validate(filename, data)
    except (docstore.DocumentTooLarge, docstore.UnsupportedDocumentType) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Fail before storing the blob if the card is gone (no orphan on disk).
    with db.repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)

    sha = await asyncio.to_thread(docstore.store_bytes, data, db.data_dir)
    with db.repos() as repos:
        doc = repos.documents.get_or_create(
            sha256=sha,
            byte_size=len(data),
            mime_type=docstore.mime_for_filename(filename),
            original_filename=filename,
        )
        repos.application_documents.set(application_id, kind, doc.id)
        return _application_dto(repos, repos.applications.get(application_id))


@router.delete("/api/applications/{application_id}/documents/{kind}")
async def detach_application_document(
    request: Request, application_id: str, kind: str
) -> dto.ApplicationDTO:
    """Detach the (application, kind) resume/cover file — the ✕ on the attached-
    file chip. The content-addressed blob stays (it may back other cards)."""
    if kind not in PACKET_KINDS:
        raise HTTPException(
            status_code=422, detail=f"kind must be one of {list(PACKET_KINDS)}"
        )
    db = _db(request)
    with db.repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)
        repos.application_documents.delete(application_id, kind)
        return _application_dto(repos, repos.applications.get(application_id))


@router.get("/api/documents/{document_id}")
async def download_document(request: Request, document_id: str) -> FileResponse:
    """Serve an uploaded document verbatim (the resume/cover a user attached to a
    manual card). Content-addressed on disk; streamed with its original name."""
    db = _db(request)
    with db.repos() as repos:
        doc = _found(repos.documents.get(document_id), "document", document_id)
        sha, mime, filename = doc.sha256, doc.mime_type, doc.original_filename
    path = docstore.blob_path(sha, db.data_dir)
    if not path.exists():
        raise HTTPException(status_code=404, detail="document blob is missing on disk")
    return FileResponse(
        path,
        media_type=mime or "application/octet-stream",
        filename=filename or sha,
    )


@router.post("/api/applications/{application_id}/packet", status_code=202)
async def generate_packet(
    request: Request, application_id: str, payload: dto.PacketRequest
) -> dto.ApplicationDTO:
    """Manual/regenerate packet build (US-TL-02) — supersedes prior artifacts."""
    db = _db(request)
    with db.repos() as repos:
        app = _found(repos.applications.get(application_id), "application", application_id)
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


@router.patch("/api/applications/{application_id}/artifacts/{kind}")
async def patch_artifact(
    request: Request, application_id: str, kind: str, payload: dto.ArtifactPatch
) -> dto.ApplicationDTO:
    """Persist an edited variant + the Approve-and-Save flip (US-RES-02 / FR-RES-02).

    Targets the head (non-superseded) artifact of `kind` for this application.
    `markdown` overwrites the text (edits apply only to this variant — the master
    is untouched); `approved` stamps/clears `approved_at` (the `ready ⇄ approved`
    flip). Per-artifact, so the resume and cover letter are approved separately."""
    if kind not in PACKET_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown artifact kind {kind!r}")
    with _db(request).repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)
        head = next(
            (
                a
                for a in repos.artifacts.list_for_application(application_id)
                if a.kind == kind and a.superseded_by is None
            ),
            None,
        )
        if head is None:
            # No generated variant exists — the user pasted or typed their own
            # (for example, from their own ChatGPT or Gemini subscription). Create
            # a manual artifact with no operation, so packetState derives to
            # `ready` (editable and approvable). An approve-only flip with no text
            # has nothing to create, so it still 404s.
            if payload.markdown is None:
                raise HTTPException(status_code=404, detail=f"no {kind} artifact to update")
            repos.artifacts.create(
                application_id,
                kind=kind,
                markdown=payload.markdown,
                notes=[],
                profile_version=_current_profile_version(repos),
                guidance_used=None,
                operation_id=None,
                approved_at=now_utc() if payload.approved else None,
            )
            repos.applications.update(application_id, last_touched_at=now_utc())
            return _application_dto(repos, repos.applications.get(application_id))
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
        app = _found(repos.applications.get(application_id), "application", application_id)
        return _application_dto(repos, app)


_SCORE_LABELS = {"failed": "Score failed", "succeeded": "Scored"}


def _ops_for_job(repos: Any, kind: str, job_id: str) -> list[Any]:
    return repos.operations.list_for_snapshot(
        kind, OP_ALL_STATES, key="job_id", value=job_id
    )


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
        app = _found(repos.applications.get(application_id), "application", application_id)
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
        existing = _found(
            repos.applications.get(application_id), "application", application_id
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
        _found(repos.applications.get(application_id), "application", application_id)
        _deleted, run_ids = delete_application_cascade(repos, application_id)
    # F-M8: the runs' on-disk artifacts (frozen PDF + step PNGs) go with the
    # rows. Best-effort + path-guarded; off-loop (rmtree blocks).
    if run_ids:
        await asyncio.to_thread(purge_run_dirs, run_ids)


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
        sched = _found(repos.schedules.get(schedule_id), "schedule", schedule_id)
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
        sched = _found(repos.schedules.get(schedule_id), "schedule", schedule_id)
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
        # The re-read is unreachable-None — same txn — but keeps the type honest.
        updated = _found(repos.schedules.get(schedule_id), "schedule", schedule_id)
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
    if kind == "linkedin_search":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/linkedin/search to run a logged-in LinkedIn job search",
        )
    if kind == "contact_sync":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/networking/contact-sync to refresh contact statuses",
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
        op = _found(repos.operations.get(operation_id), "operation", operation_id)
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


@router.post("/api/operations/{operation_id}/cancel", status_code=202)
async def cancel_operation(request: Request, operation_id: str) -> dto.OperationAccepted:
    """Cancel an operation (F-M7): a still-`queued` op (any kind) is cancelled
    outright; a `running` one is accepted ONLY for the kinds that cooperatively
    poll the cancel token (score/tailor/cover — `CANCELLABLE_RUNNING_KINDS`),
    landing `cancelled` at the entrypoint's next checkpoint. 404 for an unknown
    id; 409 when there is nothing this endpoint can honestly cancel — the op is
    already terminal, or it is running a kind that never observes the token
    (a running `apply` is cancelled via POST /api/apply-runs/{id}/cancel)."""
    runner = _runner(request)
    db = _db(request)
    with db.repos() as repos:
        op = _found(repos.operations.get(operation_id), "operation", operation_id)
        kind = op.kind
        # A queued `apply` op carries a durable ApplyRun row (start_apply creates
        # it before submit). If we cancel the op here, that row must be finalized
        # too — resolve its id now (snapshot `run_id`, else the operation link).
        apply_run_id = ""
        if kind == "apply":
            apply_run_id = str((op.input_snapshot or {}).get("run_id") or "")
            if not apply_run_id:
                existing = repos.apply_runs.get_by_operation(operation_id)
                apply_run_id = existing.id if existing is not None else ""
    if not runner.cancel(operation_id):
        raise HTTPException(
            status_code=409, detail=f"operation {operation_id!r} is not cancellable"
        )
    # Cancelling a QUEUED `apply` op strands its ApplyRun row live forever
    # (the entrypoint that would finalize it never runs), so start_apply's
    # single-flight keeps returning the dead run on every later Apply click.
    # Land it `interrupted` (never `failed`) — a user cancel. Guarded to ACTIVE
    # rows, so a run the entrypoint already finalized is left untouched. Only a
    # queued apply reaches here: a running apply is refused above (409) and
    # cancelled via POST /api/apply-runs/{id}/cancel instead (2026-07-25).
    if kind == "apply" and apply_run_id:
        finalize_run_interrupted(db, apply_run_id, "cancelled before the run started")
    # Honest post-cancel state: a queued op is already `cancelled`; a running
    # one is still `running` until its next cooperative checkpoint.
    with db.repos() as repos:
        op = repos.operations.get(operation_id)
        state = op.state if op is not None else "cancelled"
    return dto.OperationAccepted(id=operation_id, kind=kind, state=state)


# The ledger's per-row human subjects (US-LOG-01 legibility, maintainer
# directive 2026-08-03): WHICH entity each operation acted on, resolved from
# the row's own snapshot/result refs with one IN query per table per page
# (the `_application_dtos` batch-loader pattern — never per-row queries). A row
# whose refs don't resolve (historical snapshots, deleted entities) gets no
# subject and renders as before — nothing is fabricated. Every field is
# verbatim entity data; the frontend adds count nouns / mode names via i18n.

_JOB_SUBJECT_KINDS = frozenset({"score", "tailor", "cover"})
_CONTACT_SUBJECT_KINDS = frozenset({"draft", "send"})


def _job_subject(job: Any | None) -> dto.OperationSubjectDTO | None:
    if job is None:
        return None
    return dto.OperationSubjectDTO(
        label=job.title, context=job.company or None, href=job.canonical_url or None
    )


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _operation_subject(
    op: Any, *, jobs: dict[str, Any], contacts: dict[str, Any], applications: dict[str, Any]
) -> dto.OperationSubjectDTO | None:
    snap = op.input_snapshot or {}
    ref = op.result_ref or {}
    kind = op.kind

    if kind in _JOB_SUBJECT_KINDS:
        return _job_subject(jobs.get(str(snap.get("job_id") or "")))
    if kind == "apply":
        app = applications.get(str(snap.get("application_id") or ""))
        return _job_subject(jobs.get(app.job_id) if app is not None else None)
    if kind in _CONTACT_SUBJECT_KINDS:
        contact = contacts.get(str(snap.get("contact_id") or ""))
        if contact is None:
            return None
        job = jobs.get(str(snap.get("job_id") or ""))
        context = None
        if job is not None:
            context = f"{job.title} · {job.company}" if job.company else job.title
        # `send` carries the outgoing text in its snapshot; `draft` returns the
        # drafted preview in result_ref — either goes to the EXPANDED row only.
        detail = str(snap.get("message") or "") if kind == "send" else str(ref.get("message") or "")
        slug = networker_ops._public_id_from_url(contact.linkedin_url)
        return dto.OperationSubjectDTO(
            label=contact.name or slug,
            href=f"https://www.linkedin.com/in/{slug}" if slug else None,
            context=context,
            detail=detail or None,
        )
    if kind == "discover":
        label = str(
            ref.get("company_name") or snap.get("company_name") or snap.get("company") or ""
        )
        count = _int_or_none(ref.get("count"))
        if not label and count is None:
            return None
        return dto.OperationSubjectDTO(label=label, count=count)
    if kind == "linkedin_search":
        queries = ref.get("queries")
        label = ", ".join(str(q) for q in queries) if isinstance(queries, list) else ""
        mode = str((ref.get("search_cursor") or {}).get("mode") or snap.get("mode") or "")
        count = _int_or_none((ref.get("scan") or {}).get("persisted"))
        if not label and not mode and count is None:
            return None
        return dto.OperationSubjectDTO(label=label, context=mode or None, count=count)
    if kind == "scan":
        count = _int_or_none((ref.get("scan") or {}).get("persisted"))
        if count is None:
            return None
        sources = ref.get("per_source") or {}
        return dto.OperationSubjectDTO(context=", ".join(sorted(sources)) or None, count=count)
    if kind == "contact_sync":
        count = _int_or_none(ref.get("synced"))
        if count is None:
            return None
        transitions = ref.get("transitions") or {}
        detail = ", ".join(
            f"{key.replace('->', ' → ')} ×{value}" for key, value in sorted(transitions.items())
        )
        return dto.OperationSubjectDTO(count=count, detail=detail or None)
    if kind == "archive_stale_contacts":
        count = _int_or_none(ref.get("archived_count"))
        return dto.OperationSubjectDTO(count=count) if count is not None else None
    if kind == "cleanup_trash":
        if not ref:
            return None
        count = sum(
            _int_or_none(ref.get(key)) or 0
            for key in (
                "tombstoned_count",
                "expired_count",
                "expired_deleted_count",
                "purged_applications_count",
            )
        )
        return dto.OperationSubjectDTO(count=count)
    if kind == "linkedin_login":
        connected_as = str(ref.get("connected_as") or "")
        return dto.OperationSubjectDTO(label=connected_as) if connected_as else None
    if kind == "watch_company":
        label = str(snap.get("company") or snap.get("url") or "")
        if not label:
            return None
        return dto.OperationSubjectDTO(label=label, href=str(snap.get("url") or "") or None)
    return None


def _ledger_subjects(repos: Any, ops: list[Any]) -> dict[str, dto.OperationSubjectDTO]:
    """Batched subject pass over a page of ledger rows: gather every ref first,
    then ONE IN query per table (applications → jobs, contacts)."""
    job_ids: set[str] = set()
    contact_ids: set[str] = set()
    application_ids: set[str] = set()
    for op in ops:
        snap = op.input_snapshot or {}
        if (op.kind in _JOB_SUBJECT_KINDS or op.kind in _CONTACT_SUBJECT_KINDS) and snap.get(
            "job_id"
        ):
            job_ids.add(str(snap["job_id"]))
        if op.kind in _CONTACT_SUBJECT_KINDS and snap.get("contact_id"):
            contact_ids.add(str(snap["contact_id"]))
        if op.kind == "apply" and snap.get("application_id"):
            application_ids.add(str(snap["application_id"]))
    applications = repos.applications.get_many(sorted(application_ids))
    job_ids.update(app.job_id for app in applications.values())
    jobs = repos.jobs.get_many(sorted(job_ids))
    contacts = repos.contacts.get_many(sorted(contact_ids))
    subjects: dict[str, dto.OperationSubjectDTO] = {}
    for op in ops:
        subject = _operation_subject(
            op, jobs=jobs, contacts=contacts, applications=applications
        )
        if subject is not None:
            subjects[op.id] = subject
    return subjects


@router.get("/api/operations")
async def list_operations(request: Request, limit: int = 100) -> list[dto.OperationDTO]:
    """Recent operations — the ledger the Logs/Analytics surfaces read (§10),
    each row carrying its batched-resolved human subject (US-LOG-01)."""
    with _db(request).repos() as repos:
        ops = repos.operations.list_recent(limit)
        subjects = _ledger_subjects(repos, ops)
        return [dto.operation_dto(op, subjects.get(op.id)) for op in ops]


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
        op = _found(repos.operations.get(operation_id), "operation", operation_id)
        return dto.operation_dto(op, _ledger_subjects(repos, [op]).get(op.id))


# -- applications: networking tab -------------------------------------------

@router.get("/api/applications/{application_id}/networking")
async def application_networking(
    request: Request, application_id: str
) -> list[dto.NetworkingContactDTO]:
    """The referral contacts linked to this role + their statuses — the detail
    modal's Networking tab (US-TR-03, shown only when LinkedIn is ON)."""
    with _db(request).repos() as repos:
        app = _found(repos.applications.get(application_id), "application", application_id)
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
        existing = _found(repos.contacts.get(contact_id), "contact", contact_id)
        # Manual wins (US-NW-12): a user-driven column move stamps the status as
        # `manual` so the contact-status sync engine won't immediately override it.
        if "connection_status" in fields:
            fields["profile_payload"] = payload_with_status_meta(existing, "manual", now_utc())
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


def _require_any_linkedin_feature(repos: Any) -> None:
    """Gate for the shared-session lifecycle routes (connect / resume).

    These were ungated (posture doc §4 #8): anything reaching the sidecar could
    open a real browser at linkedin.com, or clear the 24 h rate-limit backoff,
    with both features switched off. The session exists only to serve one of the
    two opt-ins, so at least one must be on."""
    referral_on, search_on = linkedin_feature_flags(repos)
    if not (referral_on or search_on):
        raise HTTPException(
            status_code=403,
            detail=(
                "No LinkedIn feature is enabled — turn on Referral Outreach or "
                "LinkedIn job search in Settings first."
            ),
        )


def _require_job_search_opt_in(repos: Any) -> None:
    """The logged-in job search has its OWN toggle and its OWN typed ack —
    first-class preference columns since 2026-08-02 (they used to live in the
    free-form `ui_state` blob, where a frontend key rename could silently flip
    a safety gate; posture doc §4 #8 history)."""
    _, search_on = linkedin_feature_flags(repos)
    if not search_on:
        raise HTTPException(
            status_code=403,
            detail=(
                "LinkedIn job search is disabled — enable it in "
                "Settings → Discover jobs first."
            ),
        )
    if repos.preferences.get_or_create().linkedin_search_ack_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "LinkedIn job search needs its risk acknowledgement — re-enable "
                "it in Settings → Discover jobs to record it."
            ),
        )


@router.get("/api/jobs/{job_id}/referrals/candidates")
async def list_referral_candidates(
    request: Request, job_id: str
) -> dto.ReferralCandidatesDTO:
    """The find-referrals popup candidate list for one role (US-NW-09). Contacts
    at the job's company + per-contact template drafts + already-reached derived
    from the OutreachLog. Run discover first to populate candidates."""
    with _db(request).repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
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
        # Recover the last discover's outcome (2026-07-17) so a Save-triggered
        # background discover that needed company confirmation — or found
        # nobody — doesn't reopen as a blank start screen.
        discover_state = "never"
        company_confirm: list[dict[str, Any]] = []
        confirm_url_failed = False
        discover_ops = repos.operations.list_for_snapshot(
            "discover", {"succeeded"}, key="job_id", value=job_id
        )
        if discover_ops:
            latest = max(discover_ops, key=lambda op: op.created_at)
            ref = latest.result_ref or {}
            if ref.get("needs_company_confirm"):
                discover_state = "confirm"
                company_confirm = list(ref.get("candidates") or [])
                confirm_url_failed = bool(ref.get("url_failed"))
            elif candidates:
                discover_state = "found"
            else:
                discover_state = "empty"
        elif candidates:
            discover_state = "found"
    # 1st → 2nd → 3rd degree ordering (US-NW-09 sort).
    candidates.sort(key=lambda c: (c.degree if c.degree is not None else 99))
    return dto.ReferralCandidatesDTO(
        job_id=job_id, company=company, candidates=candidates,
        discover_state=discover_state, company_confirm=company_confirm,
        confirm_url_failed=confirm_url_failed,
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
        job = _found(repos.jobs.get(job_id), "job", job_id)
        company = job.company
        # Single-flight the plain boot discover per job (NFR-LI account safety +
        # the confirm→ask-again loop fix): if an un-confirmed discover for this job
        # is already queued/running, reuse it rather than launching a second live
        # LinkedIn scan. A confirm (URN/URL) always runs — it supersedes the boot.
        if not is_confirm:
            for op in repos.operations.list_for_snapshot(
                "discover", OP_ACTIVE_STATES, key="job_id", value=job_id
            ):
                snap = op.input_snapshot or {}
                if not (snap.get("company_urn") or snap.get("company_url")):
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
        _found(repos.contacts.get(contact_id), "contact", contact_id)
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
            _found(repos.contacts.get(c.contact_id), "contact", c.contact_id)
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
        active_sends = repos.operations.list_for_snapshot(
            "send", OP_ACTIVE_STATES, key="job_id", value=payload.job_id
        )
        inflight = {
            (op.input_snapshot or {}).get("contact_id") for op in active_sends
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
    """Rolling outreach quota for the popup counter (US-NW-09/10).

    Used-counts AND caps both come from the package's enforcing ledger
    (maintainer 2026-08-02, closing the "divergent ledgers" item): the popup
    can never show head-room the send path will refuse. `OutreachLog` stays the
    per-send product history; it is no longer recounted as a quota source.
    Zero LinkedIn traffic — a local file read, off the event loop."""
    with _db(request).repos() as repos:
        session = repos.linkedin_session.get()
        prefs = repos.preferences.get_or_create()
        profile = networker_ops.resolve_pacing_profile(repos, session=session)
        connected = bool(prefs.voyager_risk_marker_on) and (
            session is not None and session.status == "valid"
        )
    quota = await asyncio.to_thread(networker_ops.linkedin_quota_snapshot, profile)
    return dto.quota_dto(connected=connected, quota=quota)


@router.post("/api/networking/contact-sync", status_code=202)
async def networking_contact_sync(
    request: Request, force: bool = False
) -> dto.ContactSyncAccepted:
    """Refresh LinkedIn contact statuses for the Networking kanban (US-NW-12 /
    FR-NW-15) — **user-initiated only**.

    This replaces the old 12 h `contact_sync` schedule, which touched LinkedIn
    with nobody present (`docs/internal/linkedin-posture.md` §1). Two callers:

    - the explicit **Sync** button, which passes `force=true` and always runs —
      an on-demand refresh the user asked for, no more LinkedIn traffic than
      them opening linkedin.com and looking at their invitations themselves;
    - opening the **Networking** surface, which passes `force=false` and is
      throttled to `CONTACT_SYNC_MIN_INTERVAL_MINUTES` so navigating back and
      forth cannot turn into a request loop.

    Already-running syncs are joined rather than duplicated, so a double click
    or a remount mid-sync does not fan out.
    """
    with _db(request).repos() as repos:
        _require_networking_enabled(repos)
        session = repos.linkedin_session.get()
        if session is None or session.status != "valid":
            raise HTTPException(
                status_code=409,
                detail="No valid LinkedIn session — connect in Settings first.",
            )
        in_flight = repos.operations.any_in_flight("contact_sync")
        last = repos.operations.latest_by_kind("contact_sync")

    if in_flight:
        return dto.ContactSyncAccepted(state="already_running")

    if not force and last is not None:
        min_gap = timedelta(minutes=CONTACT_SYNC_MIN_INTERVAL_MINUTES)
        if last.created_at + min_gap > now_utc():
            # Not an error: the kanban the user is looking at was refreshed
            # recently enough. The Sync button is right there if they disagree.
            return dto.ContactSyncAccepted(state="throttled")

    operation_id = _runner(request).submit("contact_sync", {})
    return dto.ContactSyncAccepted(id=operation_id, state="queued")


def _linkedin_session_base(repos: Any) -> tuple[dto.LinkedInSessionDTO, Any]:
    """The session DTO minus `rate_limits`, plus the resolved pacing profile.

    Built INSIDE the caller's repos context (ORM rows detach on exit). The
    caller then fills `rate_limits` via
    `asyncio.to_thread(networker_ops.linkedin_caps_snapshot, profile)` — the
    snapshot reads the pacing-ledger file, which must not block the event loop
    (async-first rule)."""
    session = repos.linkedin_session.get()
    prefs = repos.preferences.get_or_create()
    profile = networker_ops.resolve_pacing_profile(repos, session=session)
    base = dto.linkedin_session_dto(
        session, enabled=bool(prefs.voyager_risk_marker_on),
        cursor=repos.linkedin_search_cursor.get(),
    )
    return base, profile


async def _linkedin_session_response(request: Request) -> dto.LinkedInSessionDTO:
    with _db(request).repos() as repos:
        base, profile = _linkedin_session_base(repos)
    base.rate_limits = dto.rate_limits_dto(
        await asyncio.to_thread(networker_ops.linkedin_caps_snapshot, profile)
    )
    return base


@router.get("/api/linkedin/session")
async def linkedin_session(request: Request) -> dto.LinkedInSessionDTO:
    """LinkedIn session + master-toggle state (US-NW-09 / US-SET-06 / FR-SET-03).
    Reads the persisted session (fast — local only); the popup send path
    unlocks only when enabled AND status == 'valid'."""
    return await _linkedin_session_response(request)


@router.post("/api/linkedin/connect", status_code=202)
async def linkedin_connect(
    request: Request, payload: dto.LinkedInConnectRequest | None = None
) -> dto.OperationAccepted:
    """Start the headed-login session capture (US-SET-06 as-built). Enqueues the
    exclusive `linkedin_login` op — a visible browser opens at LinkedIn's login
    page; the user logs in themselves (the password never touches finds-you-jobs).
    `login_url` (maintainer/tests only) overrides the target with a LOCAL fixture.

    Gated on at least one LinkedIn feature being enabled: this route *opens a
    real browser at linkedin.com*, and it accepted that request with both
    opt-ins off until 2026-08-01 (posture doc §4 #8)."""
    with _db(request).repos() as repos:
        _require_any_linkedin_feature(repos)
    snap: dict[str, Any] = {}
    if payload is not None and payload.login_url:
        snap["login_url"] = payload.login_url
    if payload is not None and payload.timeout_s:
        snap["timeout_s"] = payload.timeout_s
    operation_id = _runner(request).submit("linkedin_login", snap)
    return dto.OperationAccepted(id=operation_id, kind="linkedin_login", state="queued")


# The one-page-of-25 invariant is owned by the package
# (`pacing.MAX_JOBS_PER_SEARCH`, enforced in `worker.search_jobs`) — the route
# carries no size knob at all (maintainer directive 2026-08-01: a smaller limit
# never made the request smaller, it only discarded received rows while implying
# a lighter footprint).


@router.post("/api/linkedin/search", status_code=202)
async def linkedin_search(
    request: Request, payload: dto.LinkedInSearchRequest | None = None
) -> dto.OperationAccepted:
    """Run a one-shot logged-in LinkedIn job search (discovery-expansion #6).

    User-clicked only — never a scheduled scan (scheduled scans must never touch
    a logged-in session). Gated server-side on **this feature's own** toggle +
    typed ack and a connected session; otherwise a clear error, not a silent
    no-op. (It used to check the *Referral Outreach* toggle instead — the wrong
    consent, in both directions: enabling referrals alone unlocked searches the
    user never acknowledged, while a search-only user was refused. Posture doc
    §4 #8.) Results land in the same discovery funnel as every other source.

    `mode` (2026-08-01): `fresh` runs page 0 from current prefs and resets the
    pagination cursor; `next` continues the last Fresh search's snapshot from
    each pair's own offset. Next is refused (409) when no continuable cursor
    exists — never run, expired past `SEARCH_CURSOR_TTL`, or every pair
    exhausted. The op re-checks the same precondition (self-gate)."""
    mode = payload.mode if payload is not None else "fresh"
    with _db(request).repos() as repos:
        _require_job_search_opt_in(repos)
        session = repos.linkedin_session.get()
        if session is None or session.status != "valid":
            raise HTTPException(status_code=409, detail=SEARCH_NOT_CONNECTED)
        profile = networker_ops.resolve_pacing_profile(repos, session=session)
        if mode == "next":
            state = dto.linkedin_search_cursor_dto(repos.linkedin_search_cursor.get())
            if state is None:
                raise HTTPException(status_code=409, detail=SEARCH_NO_CURSOR)
            if state.expired:
                raise HTTPException(status_code=409, detail=SEARCH_CURSOR_EXPIRED)
            if state.exhausted:
                raise HTTPException(status_code=409, detail=SEARCH_CURSOR_EXHAUSTED)
    # Self-imposed pages/hour throttle: refuse when the hourly budget is spent
    # (both modes — every search fetches a page). The worker enforces this too;
    # the route surfaces it as a clean 429 rather than a 0-result search. Ledger
    # file read → off the event loop.
    snapshot = await asyncio.to_thread(networker_ops.linkedin_caps_snapshot, profile)
    if snapshot["job_search_hour_remaining"] <= 0:
        raise HTTPException(
            status_code=429,
            detail="LinkedIn job-search hourly limit reached — wait for it to "
                   "reset, or raise it in Settings › LinkedIn self-imposed rate limits.",
        )
    operation_id = _runner(request).submit("linkedin_search", {"mode": mode})
    return dto.OperationAccepted(id=operation_id, kind="linkedin_search", state="queued")


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
        # Off the loop (async-first rule): a populated Chromium profile is
        # hundreds of MB across thousands of files — seconds of filesystem
        # work that must not starve /healthz.
        await asyncio.to_thread(shutil.rmtree, linkedin_profile_dir())
    except FileNotFoundError:
        pass
    except OSError as exc:
        get_logger().warning("linkedin disconnect: could not delete profile dir: %s", exc)
    with _db(request).repos() as repos:
        repos.linkedin_session.update(
            status="never_set", connected_as="", li_at_expires_at=None,
            last_validated_at=None, paused_until=None, paused_reason="",
        )
        # A pagination cursor without its session is meaningless — drop it.
        repos.linkedin_search_cursor.clear()
    return await _linkedin_session_response(request)


@router.post("/api/linkedin/validate")
async def linkedin_validate(request: Request) -> dto.LinkedInSessionDTO:
    """Re-check the saved session LOCALLY (li_at presence/expiry) — **never hits
    LinkedIn** (US-SET-06 Validate). Flips status to valid / expired / never_set
    and stamps `last_validated_at`."""
    with _db(request).repos() as repos:
        profile = networker_ops.resolve_pacing_profile(repos)
    # Off the loop (async-first rule): session_status is local-only, but the
    # first call lazily imports playwright.sync_api — up to ~1 s on a cold
    # packaged build.
    def _local_status() -> dict:
        driver = networker_ops.DRIVER_FACTORY(profile)
        try:
            return driver.session_status()
        finally:
            driver.close()

    info = await asyncio.to_thread(_local_status)
    status = info.get("status", "never_set")
    with _db(request).repos() as repos:
        fields: dict[str, Any] = {"status": status, "last_validated_at": now_utc()}
        if status != "valid":
            fields["connected_as"] = ""
        repos.linkedin_session.update(**fields)
    return await _linkedin_session_response(request)


@router.post("/api/linkedin/resume")
async def linkedin_resume(request: Request) -> dto.LinkedInSessionDTO:
    """Clear the voyager-owned backoff pause (Settings → Networking manual resume,
    FR-NW-05 / US-REF-09). Resets the local pacing ledger and re-validates.

    Gated: this is the one route that *switches a safety mechanism off* — it
    clears the 24 h backoff LinkedIn's own throttle signal put us in — and it ran
    ungated until 2026-08-01 (posture doc §4 #8)."""
    with _db(request).repos() as repos:
        _require_any_linkedin_feature(repos)
        profile = networker_ops.resolve_pacing_profile(repos)
    # Off the loop (async-first rule): same first-call playwright import cost
    # as validate, plus the pacing-ledger file writes.
    def _resume_and_status() -> dict:
        driver = networker_ops.DRIVER_FACTORY(profile)
        try:
            driver.resume()
            return driver.session_status()
        finally:
            driver.close()

    info = await asyncio.to_thread(_resume_and_status)
    status = info.get("status", "never_set")
    with _db(request).repos() as repos:
        repos.linkedin_session.update(
            status=status, paused_until=None, paused_reason="",
            last_validated_at=now_utc(),
        )
    return await _linkedin_session_response(request)


@router.post("/api/linkedin/rate-limits")
async def linkedin_set_rate_limits(
    request: Request, payload: dto.LinkedInRateLimitsRequest
) -> dto.LinkedInSessionDTO:
    """Set the self-imposed LinkedIn rate-limit profile (maintainer directive
    2026-08-01, replacing the New/Seasoned tier + Free/Premium plan selectors).

    The package owns the cap *values*; this stores only the user's choices:
    - **membership_type** and/or **risk_pct** — the basis. Changing either
      RESETS every per-meter override to the freshly computed default (the
      maintainer's "both reset" rule): the ceilings or the scale changed, so any
      old absolute pin is stale.
    - **override_key / override_value** — pin one `{meter}_{window}` cap to an
      absolute number (only when NOT also changing the basis).
    - **reset_overrides** — drop all pins back to the computed defaults.

    voyager still enforces the numbers (NFR-LI-02); this is the selection only."""
    from sidecar.packages.referral_outreach import MEMBERSHIPS, OVERRIDABLE, clamp_risk

    override_keys = {f"{m}_{w}" for m, w in OVERRIDABLE}
    changes_basis = payload.membership_type is not None or payload.risk_pct is not None
    with _db(request).repos() as repos:
        current = repos.linkedin_session.get_or_create()
        fields: dict[str, Any] = {}
        if changes_basis or payload.reset_overrides:
            if payload.membership_type is not None:
                if payload.membership_type not in MEMBERSHIPS:
                    raise HTTPException(
                        status_code=422,
                        detail=f"membership_type must be one of {sorted(MEMBERSHIPS)}",
                    )
                fields["membership_type"] = payload.membership_type
            if payload.risk_pct is not None:
                fields["risk_pct"] = clamp_risk(payload.risk_pct)
            # Both-reset rule: a basis change (or an explicit reset) clears every
            # override so the caps revert to the new (ceiling × risk%) default.
            fields["cap_overrides"] = {}
        elif payload.override_key is not None:
            if payload.override_key not in override_keys:
                raise HTTPException(
                    status_code=422,
                    detail=f"override_key must be one of {sorted(override_keys)}",
                )
            if payload.override_value is None or payload.override_value < 0:
                raise HTTPException(
                    status_code=422, detail="override_value must be a non-negative integer"
                )
            overrides = dict(current.cap_overrides or {})
            overrides[payload.override_key] = int(payload.override_value)
            fields["cap_overrides"] = overrides
        else:
            raise HTTPException(
                status_code=422,
                detail="provide membership_type/risk_pct, an override, or reset_overrides",
            )
        repos.linkedin_session.update(**fields)
    return await _linkedin_session_response(request)


# -- Dev tools (local testing only) ----------------------------------------
# A single-user local app on the user's own machine — these fault-injection
# endpoints power the Dev surface (US-DEV-01, dev-only): simulate an expired
# LinkedIn cookie mid-action, a crash mid-generation, and quick seed data.
# F-L4: they mutate/corrupt real state, so they answer only when the process
# was started with FYJ_DEV=1 (scripts/dev-web.mjs sets it; the packaged app
# never does) — otherwise 404, indistinguishable from an unknown route.


def _require_dev_mode() -> None:
    if os.environ.get("FYJ_DEV") != "1":
        raise HTTPException(status_code=404, detail="Not Found")


@router.post("/api/dev/linkedin/mark-session-valid")
async def dev_mark_linkedin_session_valid(request: Request) -> dict[str, Any]:
    """Set `LinkedInSession.status = "valid"` so session-gated UI can be
    exercised without a real LinkedIn login.

    Some controls (the Networking **Sync** button) only render once a session
    exists, and a real session needs an interactive human login at LinkedIn's
    own page — which a test cannot perform and must never attempt. This sets the
    one status field the UI reads, exactly like the sibling
    `/api/dev/linkedin/expire-cookie` route sets the opposite condition.

    It writes **no** cookies and **no** storage-state file. That is the honest
    part: the session is a real row that is genuinely unusable, so any code path
    that actually reaches LinkedIn fails on auth as it should. Nothing about
    LinkedIn's behaviour is faked, and no production code branches on this — the
    row is the same row a real login writes, with the same fields."""
    _require_dev_mode()
    with _db(request).repos() as repos:
        repos.linkedin_session.update(status="valid", connected_as="Dev Session")
    return {"ok": True, "status": "valid"}


@router.post("/api/dev/linkedin/seed-search-cursor")
async def dev_seed_linkedin_search_cursor(request: Request) -> dict[str, Any]:
    """Write a live (non-expired, non-exhausted) job-search pagination cursor
    so the Next-page button can be exercised without a real LinkedIn search —
    which needs a real logged-in session a test must never use. Same honesty
    contract as `mark-session-valid`: the row is the same row a real Fresh
    search writes, and nothing in production branches on this."""
    _require_dev_mode()
    with _db(request).repos() as repos:
        repos.linkedin_search_cursor.update(
            fresh_at=now_utc(),
            queries=[{"keyword": "backend engineer", "location": "Remote",
                      "next_start": 25, "exhausted": False}],
        )
    return {"ok": True}


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
    _require_dev_mode()
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
    db = _db(request)
    # Read phase — NO writes in this session: a pending write would autoflush
    # on the next query and hold SQLite's write lock while runner.submit
    # inserts on its own connection (the 2026-07-17 "database is locked").
    with db.repos() as repos:
        app_row = _found(
            repos.applications.get(application_id), "application", application_id
        )
        job = _found(repos.jobs.get(app_row.job_id), "job", app_row.job_id)
        active = repos.apply_runs.latest_for_application(application_id)
        if active is not None and active.status in APPLY_RUN_ACTIVE_STATUSES:
            # Single-flight per card: reopening the companion binds to the
            # active run instead of double-launching a browser (§8.2).
            return dto.apply_run_dto(active)
        needs_intent = app_row.intent != "apply"
        job_url = job.canonical_url

    snapshot: dict[str, Any] = {"application_id": application_id}
    if payload.retry_of_run_id:
        snapshot["retry_of_run_id"] = payload.retry_of_run_id
    if payload.dev:
        snapshot.update({f"dev_{k}": v for k, v in payload.dev.items()})

    # Write phase — settle the exclusive intent (roadmap §5.1) and create the
    # durable run BEFORE enqueueing the op, in its own committed txn (no
    # pending write is held across runner.submit — the 2026-07-17 locked-DB
    # discipline). The worker can dispatch within milliseconds of submit, and
    # it must adopt THIS row (by snapshot `run_id`): creating the row after
    # submit raced the op's `get_by_operation` lookup, which then minted a
    # duplicate run while the panel watched the route's row sit `queued`
    # forever. Honest initial state: QUEUED until the op actually starts.
    with db.repos() as repos:
        if needs_intent:
            repos.applications.update(application_id, intent="apply")
        run = repos.apply_runs.create(
            application_id,
            retry_of_run_id=payload.retry_of_run_id,
            source_url=job_url,
            status="queued",
            phase="queued",
        )
        run_id = run.id
    snapshot["run_id"] = run_id
    try:
        operation_id = runner.submit("apply", snapshot)
    except Exception as exc:
        # Enqueue failed (2026-07-25): the just-created row would strand
        # `queued` until boot sweep — land it terminal-failed, then let the
        # error surface exactly as it otherwise would (flight-recorded 500).
        finalize_run_failed(db, run_id, f"enqueue failed — {type(exc).__name__}: {exc}")
        raise

    with db.repos() as repos:
        # Attach the ledger link; the op writes the same value on adoption, so
        # whichever lands second is a no-op. Progress columns are untouched.
        run = repos.apply_runs.update(run_id, operation_id=operation_id)
        return dto.apply_run_dto(run)


@router.get("/api/applications/{application_id}/apply-runs")
async def list_apply_runs(
    request: Request, application_id: str
) -> list[dto.ApplyRunDTO]:
    with _db(request).repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)
        return [
            dto.apply_run_dto(r)
            for r in repos.apply_runs.list_for_application(application_id)
        ]


@router.get("/api/apply-runs/{run_id}")
async def get_apply_run(request: Request, run_id: str) -> dto.ApplyRunDTO:
    """The run snapshot — a reopened companion fetches this instead of
    depending on having seen every prior SSE event (§9.2)."""
    with _db(request).repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
        return dto.apply_run_dto(run)


@router.get("/api/apply-runs/{run_id}/screenshots/{index}")
async def get_apply_run_screenshot(
    request: Request, run_id: str, index: int
) -> FileResponse:
    """Serve one evidence PNG by index. Paths come from the run row only —
    never from the client — so this cannot read arbitrary files."""
    with _db(request).repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
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
    db = _db(request)
    cancelled_queued = False
    with db.repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
        if run.operation_id and run.operation_id in APPLY_CONTROL:
            # In flight: cooperative — the loop notices between steps.
            APPLY_CONTROL[run.operation_id].cancel()
        elif run.operation_id and run.status == "queued":
            # Still queued: cancel the op outright and land the run honestly.
            cancelled_queued = runner.cancel(run.operation_id)
        if not cancelled_queued:
            return dto.apply_run_dto(run)
    # The landing goes through the ONE interrupted-transition implementation
    # (D-A7) — the same call the generic operations-cancel route makes, so the
    # two cancel surfaces can never write different terminal state.
    finalize_run_interrupted(db, run_id, "cancelled before the run started")
    with db.repos() as repos:
        return dto.apply_run_dto(_found(repos.apply_runs.get(run_id), "run", run_id))


@router.post("/api/apply-runs/{run_id}/attest")
async def attest_apply_run(
    request: Request, run_id: str, payload: dto.ApplyAttestRequest
) -> dto.ApplyRunDTO:
    """The human's word after the P1 handoff (§8.4): 'I submitted' records a
    user-attested submission and advances the card to Applied; 'didn't submit'
    leaves the card in its pre-submission column with the honest run result."""
    with _db(request).repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
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
            advance_card_to_applied(repos, run.application_id, by="user_attested")
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
    from ..events import operation_event
    from ..runner.runner import RESTART_NOTE

    _require_dev_mode()
    hub = getattr(request.app.state, "hub", None)
    failed: list[str] = []
    with _db(request).repos() as repos:
        for op in repos.operations.list_by_state("running"):
            repos.operations.mark_failed(op.id, error=RESTART_NOTE)
            failed.append(op.id)
            if hub is not None:
                # Canonical payload shape (`id`, like every runner publish) —
                # this route used to ship `operation_id`, the one divergence
                # that kept OperationEventPayload.id optional.
                hub.publish(operation_event(op.id, op.kind, "failed", error=RESTART_NOTE))
    return {"ok": True, "failed": failed, "count": len(failed)}


@router.post("/api/dev/operations/seed-queued", status_code=201)
async def dev_seed_queued_operation(request: Request) -> dict[str, Any]:
    """Create a `queued` operation ROW directly — without pumping the runner —
    so it STAYS queued until something else submits work. Lets the Logs Stop
    control (F-M7) be exercised deterministically in e2e (the generic enqueue
    dispatches almost immediately, so a queued row is otherwise a race). Kind
    `cleanup_trash`: zero-LLM and harmless if a later pump does dispatch it.
    Dev-only."""
    from ..events import operation_event

    _require_dev_mode()
    hub = getattr(request.app.state, "hub", None)
    with _db(request).repos() as repos:
        op = repos.operations.create("cleanup_trash", {})
        op_id, kind = op.id, op.kind
    if hub is not None:
        hub.publish(operation_event(op_id, kind, "queued"))
    return {"ok": True, "id": op_id, "kind": kind, "state": "queued"}


@router.post("/api/dev/seed-application", status_code=201)
async def dev_seed_application(request: Request) -> dict[str, Any]:
    """Create a sample Job + Saved Application so the Tracker has a card to drive
    (drag, generate, apply) without a live scrape/score. Dev-only."""
    import uuid

    _require_dev_mode()

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
