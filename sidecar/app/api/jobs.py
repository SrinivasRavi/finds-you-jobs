"""Job routes: the board, the scan progress, pasted jobs, duplicates, the
trash, score factors and explanations, and the scoring retry.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from sidecar.modules.scraper import ScraperError, probe_url
from sidecar.modules.scraper.canonical import canonicalize_url
from sidecar.modules.scraper.filters import passes_company, passes_content, passes_salary
from sidecar.modules.scraper.fingerprint import (
    is_probable_cross_listing,
    simhash64,
    similarity,
    to_signed64,
)
from sidecar.modules.scraper.identity import content_hash
from sidecar.modules.scraper.quality import evaluate as quality_evaluate
from sidecar.modules.scraper.types import ScanPrefs

from ..db.base import now_utc
from ..db.models import OP_ACTIVE_STATES
from ..registry import networker_ops as networker_ops
from ..registry.persistence import (
    delete_job_cascade,
    saved_job_ids,
    scoring_mode,
    tombstone_url,
)
from . import dto
from .deps import db as _db
from .deps import engines as _engines
from .deps import found as _found
from .deps import runner as _runner

router = APIRouter()

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
def list_jobs(request: Request, feed_state: str = "active") -> list[dto.JobDTO]:
    # Bounded (200 rows); a plain `def` handler runs in Starlette's threadpool
    # automatically — consistent with the board/applications pattern
    # (async-first rule / F-H2).
    with _db(request).repos() as repos:
        jobs = repos.jobs.list(feed_state=feed_state or None)
        score_op_states = repos.operations.score_states_by_job()
        lens = dto.build_weight_lens(repos, [j.id for j in jobs])
        dtos = [
            dto.job_dto(j, score_op_states=score_op_states.get(j.id), lens=lens)
            for j in jobs
        ]
        new_ids = _scan_new_ids(repos.operations.latest_succeeded_by_kind("scan"))
        _stamp_duplicates(repos, dtos)
    for d in dtos:
        d.is_new = d.id in new_ids
    _sort_board(dtos)
    return dtos


def _stamp_duplicates(repos: Any, dtos: list[dto.JobDTO]) -> None:
    """Fill each job's duplicate count and notice flag in 1 query."""
    copies, noticed = repos.duplicates.open_summary([d.id for d in dtos])
    for d in dtos:
        d.duplicates = copies.get(d.id, 0)
        d.duplicate_notice = d.id in noticed


def _display_score_value(job: Any, lens: dto.WeightLens | None = None) -> int:
    """The number a board row sorts on: its 1 score, else -1 so unscored rows land
    last. With `lens`, the
    number the row will SHOW under the user's weights, so the board sorts
    before it paginates on the same value each card displays."""
    value = job.score
    if value is None:
        return -1
    if lens is not None:
        reweighted = lens.total_for(job, job.score_method or "llm")
        if reweighted is not None:
            return reweighted
    return value


# Board-eligible feed states: active + expired (Expired stays on the board,
# greyed — FR-SYS-03). `removed` (Trash) and hard-deletes are off the board.
_BOARD_FEED_STATES = ["active", "expired"]
_BOARD_PAGE_SIZE = 50


def _suppressed_by_excludes(state: Any, hard_excludes: dict, jobs: list[Any]) -> set[str]:
    """Job ids the user's hard excludes hide from the board, retroactively; hidden,
    never deleted. Uses the scan's own matchers so board and scan agree. About 87 µs
    per job, so the result is cached on app.state and recomputed only when the
    excludes or the feed change."""
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


def _board_search_haystack(job: Any, deep: bool) -> str:
    """FR-JB-13: a board row's searchable text. Shallow covers title, company and
    location; deep adds the JD and the displayed rating's texts. Reads the ORM row,
    so the DTO build waits until after search and pagination (F-H2)."""
    parts = [job.title, job.company, job.location]
    if deep:
        parts += [job.salary or "", job.source_adapter, job.description]
        parts += [
            str(r.get("text", "")) if isinstance(r, dict) else str(r) for r in job.score_reasons
        ]
        parts.append(job.score_breakdown_md)
    return "\n".join(parts).lower()


def _posted_within(job: Any, days: int, now: datetime) -> bool:
    """A job with no readable posting date fails a "posted within" filter, as
    the browser's filter did: the user asked for recent, and it can't say."""
    try:
        posted = datetime.fromisoformat(job.posted_at or "")
    except ValueError:
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return (now - posted).total_seconds() <= days * 86_400


def _board_filter(rows: list[Any], f: dto.BoardFilters) -> list[Any]:
    """The board's 5 chip filters, over every eligible row before the page is
    cut (audit finding 6). Work style reads `derive_work_style`, the card chip's
    own function, and only when asked, because it's 3 regexes per JD. Minimum
    salary reads the scan's `passes_salary`, so the board and the scan agree on
    what a salary says; no salary, or one in another currency, always passes."""
    if f.source:
        rows = [j for j in rows if j.source_adapter == f.source]
    if f.work_style == "REMOTE_FRIENDLY":
        rows = [
            j for j in rows
            if dto.derive_work_style(j.location, j.description) == "REMOTE"
            or "remote" in j.location.lower()
        ]
    elif f.work_style:
        rows = [j for j in rows if dto.derive_work_style(j.location, j.description) == f.work_style]
    if f.method == "model":
        rows = [j for j in rows if j.score_method not in (None, "keyword")]
    elif f.method == "keyword":
        rows = [j for j in rows if j.score_method == "keyword"]
    if f.posted_days > 0:
        now = now_utc()
        rows = [j for j in rows if _posted_within(j, f.posted_days, now)]
    if f.salary_min > 0:
        prefs = ScanPrefs(salary_min=f.salary_min, salary_currency="USD")
        rows = [j for j in rows if passes_salary(j.salary or "", prefs)]
    return rows


@router.get("/api/board")
def board(
    request: Request,
    page: int = 0,
    page_size: int = _BOARD_PAGE_SIZE,
    list_q: str = "",
    text_q: str = "",
    link: str = "",
    source: str = "",
    work_style: Literal["", "REMOTE", "HYBRID", "ONSITE", "REMOTE_FRIENDLY"] = "",
    method: Literal["", "model", "keyword"] = "",
    posted_days: int = 0,
    salary_min: int = 0,
    sort: Literal["match", "recency"] = "match",
) -> dto.BoardPageDTO:
    """The paginated board and its header (FR-JB-02/10): saved jobs excluded, expired
    ones greyed, 1 honest total. Search and `link` (has or none, read from
    `canonical_url`) filter before pagination, so no match hides on an unloaded
    page. A plain `def`, so off the loop; DTOs are built for the returned page only."""
    page = max(0, page)
    page_size = max(1, min(_BOARD_PAGE_SIZE, page_size))

    with _db(request).repos() as repos:
        saved = saved_job_ids(repos)
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
        score_op_states = repos.operations.score_states_by_job()
        # Scrape status/meta (FR-JB-10) — from the operations ledger, live via SSE.
        scan_running = repos.operations.any_in_flight("scan")
        last_scan = repos.operations.latest_succeeded_by_kind("scan")
        latest_scan = repos.operations.latest_by_kind("scan")
        new_ids = _scan_new_ids(last_scan)
        lens = dto.build_weight_lens(repos, [j.id for j in jobs])
    # Sort + search over the ORM rows — same key/haystack as the DTO path
    # (FR-JB-01 / FR-JB-13); the DTO is built only for the window below.
    if sort == "recency":
        rows = sorted(jobs, key=lambda j: (j.posted_at or "", j.ingested_at), reverse=True)
    else:
        rows = sorted(
            jobs,
            key=lambda j: (_display_score_value(j, lens), j.ingested_at),
            reverse=True,
        )
    # `empty` means the scrape found nothing — judged before search filtering,
    # so a search miss reads as a filter miss, not an empty scrape (FR-JB-13).
    feed_empty = len(rows) == 0
    needle = list_q.strip().lower()
    if needle:
        rows = [j for j in rows if needle in _board_search_haystack(j, deep=False)]
    needle = text_q.strip().lower()
    if needle:
        rows = [j for j in rows if needle in _board_search_haystack(j, deep=True)]
    if link == "has":
        rows = [j for j in rows if j.canonical_url]
    elif link == "none":
        rows = [j for j in rows if not j.canonical_url]
    rows = _board_filter(
        rows,
        dto.BoardFilters(
            source=source, work_style=work_style, method=method,
            posted_days=max(0, posted_days), salary_min=max(0, salary_min),
        ),
    )
    total = len(rows)
    page_rows = rows[page * page_size : page * page_size + page_size]
    window = [
        dto.job_dto(j, score_op_states=score_op_states.get(j.id), lens=lens)
        for j in page_rows
    ]
    with _db(request).repos() as repos:
        _stamp_duplicates(repos, window)
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


@router.get("/api/scan/progress")
def scan_progress(request: Request) -> dto.ScanProgressDTO:
    """Board scan and scoring progress, polled while a scan runs, assembled from the
    ledger: `scan_running`, `last_scan_at`, `new_found`, `score_pending`, and
    `score_done` (this scan's new jobs with a terminal score). Off the loop."""
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


# Honest user-facing copy for a re-add of a permanently-deleted (tombstoned)
# identity — trash is recoverable, a tombstone is final.
_TOMBSTONE_409_DETAIL = (
    "This job was permanently deleted from Trash and can't be re-added. "
    "If you still want to track it, keep a record of it outside the app."
)

_NO_IDENTITY_422_DETAIL = (
    "A job needs either a posting link or a description — without one of "
    "them there's nothing to tell it apart from the next job."
)


def _identity(canonical_url: str, description: str) -> tuple[str | None, str | None]:
    """`(canonical_url, content_hash)` for a job about to be written: 1 key per row
    (url-less-jobs.md section 5.2). A URL row keys on its URL with a NULL hash, so
    requisitions sharing boilerplate don't collide; a row with no usable URL keys on
    its description. A non-http(s) string yields no URL."""
    canonical = canonicalize_url(canonical_url) or None
    if canonical is not None:
        return canonical, None
    return None, content_hash(description) or None


@router.post("/api/jobs/preview")
def preview_job(request: Request, payload: dto.JobPreviewRequest) -> dto.JobPreviewDTO:
    """Add a job posting, step 1 (US-JB-07): fetch the URL and extract editable
    fields, not persisted; 20 s, no retry, off the loop. A tombstoned URL fails fast
    with 409; an existing one returns its stored fields instead of a re-scrape."""
    canonical = canonicalize_url(payload.url) or payload.url
    with _db(request).repos() as repos:
        if repos.tombstones.exists(canonical):
            raise HTTPException(status_code=409, detail=_TOMBSTONE_409_DETAIL)
        existing = repos.jobs.get_by_canonical_url(canonical)
        if existing is not None:
            return dto.JobPreviewDTO(
                canonical_url=existing.canonical_url or "",
                title=existing.title,
                company=existing.company,
                location=existing.location,
                description=existing.description,
                posted_at=existing.posted_at or None,
                salary=existing.salary or None,
                source_adapter=existing.source_adapter or "paste-url",
            )
    try:
        job = probe_url(payload.url, timeout_s=20)
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
def create_job(request: Request, payload: dto.JobCreate) -> dto.JobDTO:
    """Add a job posting, step 2 (US-JB-07) + programmatic ingest: persist one job with
    the same dedup/tombstone discipline as scan, then enqueue a score so the new
    row lands on the board with a fit rating."""
    canonical, digest = _identity(payload.canonical_url, payload.description)
    if canonical is None and digest is None:
        raise HTTPException(status_code=422, detail=_NO_IDENTITY_422_DETAIL)
    db = _db(request)
    enqueue_score = False
    profile_version = 1
    job_id: str | None = None
    with db.repos() as repos:
        # Tombstone = final: a permanently-deleted identity can never be
        # re-added. Trash = recoverable: a Trashed one is restored.
        if repos.tombstones.exists(canonical or ""):
            raise HTTPException(status_code=409, detail=_TOMBSTONE_409_DETAIL)
        existing = repos.jobs.get_by_identity(
            canonical_url=canonical or "", content_hash=digest or ""
        )
        if existing is not None:
            if existing.feed_state != "removed":
                return dto.job_dto(existing)  # already active — dedup, first-seen wins
            # Restore from Trash: un-trash and keep its score and history. Score it only
            # under the planner's rule (score-storage.md 4.3): no score, or a Keyword one
            # while Settings picks another method. Nothing is bought twice.
            job = repos.jobs.set_trash_state(existing.id, trashed=False)
            mode = scoring_mode(repos.preferences.get_or_create())
            needs_score = job.score is None or (
                job.score_method == "keyword" and mode != "keyword"
            )
            result = dto.job_dto(job)
            if needs_score:
                profile = repos.profile.get_current()
                if profile is not None:
                    enqueue_score = True
                    job_id, profile_version = job.id, profile.version
        else:
            # The same ingest quality checks a scanned row gets. A hand-added
            # job never passed through a scan, so until this call the red-flag
            # terms were never read on the 1 path where the text arrives out of
            # the user's own email (url-less-jobs.md 15.3). The URL-dependent
            # checks no-op on a URL-less job.
            trust_score, trust_flags = quality_evaluate(
                title=payload.title,
                location=payload.location,
                description=payload.description,
                canonical_url=canonical or "",
                posted_at=payload.posted_at,
            )
            job = repos.jobs.create(
                canonical_url=canonical,
                content_hash=digest,
                fingerprint=to_signed64(simhash64(payload.description)) or None,
                title=payload.title,
                company=payload.company,
                location=payload.location,
                description=payload.description,
                posted_at=payload.posted_at,
                salary=payload.salary,
                source_adapter=payload.source_adapter,
                origin="manual",
                trust_score=trust_score,
                trust_flags=trust_flags,
            )
            # The cross-scan duplicate check, at write (url-less-jobs.md 9.6).
            # One narrow scan here keeps the board's render free of it.
            from ..registry.persistence import flag_near_duplicate

            flag_near_duplicate(repos, job)
            profile = repos.profile.get_current()
            if profile is not None:
                enqueue_score = True
                job_id, profile_version = job.id, profile.version
            result = dto.job_dto(job)
    # Score the freshly-added (or restored-but-unscored) job so it sorts into the
    # feed (US-JB-07 → FR-JB-01).
    if enqueue_score and job_id is not None:
        _runner(request).submit(
            "score",
            {"job_id": job_id, "profile_version": profile_version},
            requested_by="user",
        )
    return result


@router.patch("/api/jobs/{job_id}")
def update_job(
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
        op_states = repos.operations.score_states_for_job(job_id)
        lens = dto.build_weight_lens(repos, [job_id])
        return dto.job_dto(job, score_op_states=op_states, lens=lens)


@router.post("/api/jobs/{job_id}/link")
def attach_job_link(
    request: Request, job_id: str, payload: dto.AttachLinkRequest
) -> dto.AttachLinkResultDTO:
    """Promotion (url-less-jobs.md section 5.4): a URL-less job gets its URL and
    becomes an ordinary URL job. The check is compound (section 6.4): equal hashes
    accept, same company and title with similarity at least 0.90 accept, anything
    else returns `mismatch`. Re-keying is 1 UPDATE, so every child survives; the old
    hash stays for re-paste lookups. Off the loop."""
    canonical = canonicalize_url(payload.url)
    if not canonical:
        raise HTTPException(
            status_code=422, detail=f"not a usable http(s) URL: {payload.url!r}"
        )
    db = _db(request)
    with db.repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
        if job.canonical_url:
            raise HTTPException(
                status_code=409,
                detail="This job already has a posting link.",
            )
        if repos.tombstones.exists(canonical):
            raise HTTPException(status_code=409, detail=_TOMBSTONE_409_DETAIL)
        card = dto.LinkCandidateDTO(
            title=job.title, company=job.company, location=job.location
        )
        job_description = job.description
        job_hash = job.content_hash or ""

    probed = None
    if not payload.confirm:
        try:
            probed = probe_url(payload.url, timeout_s=20)
        except ScraperError:
            # A JS-only page, a dead URL, or a board with no adapter. There is
            # nothing to compare against, so say so and take the user's word
            # on the next call rather than refusing outright.
            return dto.AttachLinkResultDTO(status="unreadable", card=card)

        verdict, score = _link_verdict(probed, card, job_description, job_hash)
        if not verdict:
            return dto.AttachLinkResultDTO(
                status="mismatch",
                card=card,
                pasted=dto.LinkCandidateDTO(
                    title=probed.title,
                    company=probed.company,
                    location=probed.location,
                    similarity=score,
                ),
            )

    with db.repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
        other = repos.jobs.get_by_canonical_url(canonical)
        if other is not None and other.id != job.id:
            # 2 rows want 1 URL. Say which one and write nothing: the user
            # deletes the one they don't need, the app never picks (maintainer,
            # url-less-jobs.md section 5.5).
            return dto.AttachLinkResultDTO(
                status="duplicate",
                pasted=dto.LinkCandidateDTO(
                    title=other.title, company=other.company, location=other.location
                ),
                other_job_id=other.id,
            )
        updated = repos.jobs.update(job_id, canonical_url=canonical)
        return dto.AttachLinkResultDTO(status="attached", job=dto.job_dto(updated))


def _link_verdict(
    probed: Any, card: Any, description: str, content_hash_value: str
) -> tuple[bool, float]:
    """The compound gate of section 5.4, and the similarity it measured."""
    if content_hash_value and content_hash(probed.description or "") == content_hash_value:
        return True, 1.0
    card_fp = simhash64(description or "")
    probed_fp = simhash64(probed.description or "")
    return (
        is_probable_cross_listing(
            card_fp,
            probed_fp,
            company_a=card.company,
            company_b=probed.company,
            title_a=card.title,
            title_b=probed.title,
        ),
        similarity(card_fp, probed_fp),
    )


@router.get("/api/jobs/{job_id}/duplicate")
def get_job_duplicate(request: Request, job_id: str) -> dto.DuplicatePromptDTO | None:
    """The other job this one probably duplicates, or null (url-less-jobs.md 9.6).
    1 indexed read of `job_duplicates`, from either side of the pair."""
    with _db(request).repos() as repos:
        _found(repos.jobs.get(job_id), "job", job_id)
        found = repos.duplicates.open_for(job_id)
        if found is None:
            return None
        other = repos.jobs.get(found[0])
        if other is None:
            return None
        return dto.DuplicatePromptDTO(
            job_id=job_id,
            other_job_id=other.id,
            other=dto.LinkCandidateDTO(
                title=other.title, company=other.company, location=other.location
            ),
            other_added_at=other.ingested_at,
        )


@router.post("/api/jobs/{job_id}/duplicate/dismiss")
def dismiss_job_duplicate(
    request: Request, job_id: str, payload: dto.DuplicateActionRequest
) -> dto.JobDTO:
    """"They're different." Stamped on the pair, so neither card asks again."""
    with _db(request).repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
        _found(repos.jobs.get(payload.other_job_id), "job", payload.other_job_id)
        repos.duplicates.dismiss(job_id, payload.other_job_id)
        return dto.job_dto(job)


@router.post("/api/jobs/{job_id}/tombstone")
def tombstone_job(request: Request, job_id: str) -> dto.TombstoneResultDTO:
    """Delete forever from Trash (US-JB-11): write a `Tombstone` for the job's
    canonical URL, then hard-delete the row. A tombstone is final — a future
    scan or Add a job posting can never re-surface it (FR-SYS-04). A job with no URL
    is only deleted, so its description can be pasted again."""
    with _db(request).repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
        url = job.canonical_url or ""
        tombstoned = tombstone_url(repos, job, reason="user_delete")
        delete_job_cascade(repos, job_id)
    return dto.TombstoneResultDTO(
        tombstoned=int(tombstoned), canonical_urls=[url] if tombstoned else []
    )


@router.post("/api/jobs/trash/empty")
def empty_trash(request: Request) -> dto.TombstoneResultDTO:
    """Empty Trash (US-JB-11 / FR-SYS-04): tombstone every trashed job's URL and
    delete the rows now; a job with no URL gets no tombstone. Off the loop (S-C6):
    up to 10,000 rows, 3 statements each."""
    urls: list[str] = []
    with _db(request).repos() as repos:
        for job in repos.jobs.list(feed_state="removed", limit=10_000):
            if tombstone_url(repos, job, reason="empty_trash"):
                urls.append(job.canonical_url or "")
            delete_job_cascade(repos, job.id)
    return dto.TombstoneResultDTO(tombstoned=len(urls), canonical_urls=urls)


@router.get("/api/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dto.JobDTO:
    with _db(request).repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
        op_states = repos.operations.score_states_for_job(job_id)
        lens = dto.build_weight_lens(repos, [job_id])
        return dto.job_dto(job, score_op_states=op_states, lens=lens)


@router.get("/api/jobs/{job_id}/factors")
def get_job_factors(request: Request, job_id: str) -> dto.JobFactorsDTO:
    """The bars behind one job's number (scoring.md 7.3).

    Read-only and free: every method stores its axes on the job row when it
    scores, so this never calls a model."""
    from sidecar.modules.scorer.weights import method_weights

    with _db(request).repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
        method = job.score_method or "keyword"
        rows = sorted(job.score_factors or [], key=lambda r: str(r["factor"]))
        weights = method_weights(method, {r["factor"] for r in rows})
        return dto.JobFactorsDTO(
            job_id=job_id,
            method=method,
            factors=[
                dto.ScoreFactorDTO(
                    factor=r["factor"],
                    value=r["value"],
                    kind=r.get("kind", "graded"),
                    evidence=r.get("evidence", ""),
                    code=r.get("code"),
                )
                for r in rows
            ],
            weights=dict(weights),
        )


@router.get("/api/scoring/explain-cost")
def explain_cost(request: Request) -> dto.ExplainCostDTO:
    """The price on the button before it is pressed (vision.md, cost honesty)."""
    engines = _engines(request)
    resolved = None
    if engines is not None:
        try:
            resolved = engines.resolve("score")
        except Exception:  # noqa: BLE001 — nothing routed is a state, not an error
            resolved = None
    model = getattr(resolved, "model", "") or ""
    with _db(request).repos() as repos:
        avg, samples = repos.operations.recent_score_cost(model or None)
    return dto.ExplainCostDTO(
        engine=getattr(resolved, "name", "") or "",
        model=model,
        avg_usd=avg,
        samples=samples,
        ready=resolved is not None,
    )


@router.post("/api/jobs/{job_id}/explain", status_code=202)
def explain_job(request: Request, job_id: str) -> dto.OperationAccepted:
    """1 click, 1 job, 1 AI call (scoring-build.md T13): prose for the jobs a user
    opens, at about $0.24 each. An ordinary `score` op, so the ledger, concurrency
    cap, Stop and Retry apply. The job adopts the LLM score (scoring-build.md section
    4 choice 1, pending). It can run again; both spends are in the ledger."""
    with _db(request).repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
        profile = repos.profile.get_current()
        if profile is None:
            raise HTTPException(status_code=400, detail="no master resume to score against")
        from sidecar.modules.scorer.deterministic import MIN_JD_CHARS

        if len((job.description or "").strip()) < MIN_JD_CHARS:
            # The same floor the planner uses. Explaining a job we know nothing
            # about would spend the user's money to say so (S-C27).
            raise HTTPException(
                status_code=400,
                detail="this posting has too little description to explain",
            )
        version = profile.version
    operation_id = _runner(request).submit(
        "score",
        {"job_id": job_id, "profile_version": version, "force_mode": "llm"},
        requested_by="user",
    )
    return dto.OperationAccepted(id=operation_id, kind="score", state="queued")


@router.post("/api/jobs/{job_id}/rescore", status_code=202)
def rescore_job(request: Request, job_id: str) -> dto.OperationAccepted:
    """Rescore on a board row (score-storage.md 4.4): 1 `score` op with the method in
    Settings, as the user's request. It always runs, whatever score the job has, and
    its result replaces the stored one, Keyword included."""
    with _db(request).repos() as repos:
        _found(repos.jobs.get(job_id), "job", job_id)
        profile = repos.profile.get_current()
        if profile is None:
            raise HTTPException(status_code=400, detail="no master resume to score against")
        version = profile.version
    operation_id = _runner(request).submit(
        "score",
        {"job_id": job_id, "profile_version": version, "rescore": True},
        requested_by="user",
    )
    return dto.OperationAccepted(id=operation_id, kind="score", state="queued")


# -- scoring retry ---------------------------------------------------------
# Deliberately NOT under /api/jobs/: `GET /api/jobs/{job_id}` is registered
# above and would swallow a single-segment sibling.


@router.get("/api/scoring/retryable")
def scoring_retryable(request: Request) -> dto.ScoreRetryDTO:
    """How many jobs are stuck with a spent scoring budget (S-C24). Counts
    only, never writes."""
    from ..scheduler.planner import SCORE_MAX_ATTEMPTS

    with _db(request).repos() as repos:
        return dto.ScoreRetryDTO(count=repos.jobs.count_score_exhausted(SCORE_MAX_ATTEMPTS))


@router.post("/api/scoring/retry")
def scoring_retry(request: Request) -> dto.ScoreRetryDTO:
    """Give every job that spent its budget the budget back, for the next tick: the
    recovery from a provider-wide failure (expired key, quota). Enqueues nothing.
    Jobs with no usable description are untouched (S-A6)."""
    with _db(request).repos() as repos:
        return dto.ScoreRetryDTO(reset=repos.jobs.reset_score_attempts())
