"""Lifecycle, export, system and dev-tool routes.

/healthz (open), /shutdown and the SSE `/api/events` stream fed by the runner
through the hub; the PDF export, the browser install and the scheduler
status; and the FYJ_DEV=1 fault-injection endpoints behind the Dev surface.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..db.base import now_utc
from ..events import heartbeat_stream
from ..logging_setup import get_logger
from ..registry import networker_ops as networker_ops
from . import dto
from .deps import db as _db

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# -- lifecycle -------------------------------------------------------------


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe. Open (no token) — the shell polls this (section 4.4 step 2)."""
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


# -- Dev tools (local testing only) ----------------------------------------
# Fault injection behind the Dev surface (US-DEV-01): an expired LinkedIn cookie,
# a crash mid-generation, seed data. They corrupt real state, so they answer only
# under FYJ_DEV=1, which the packaged app never sets; otherwise 404 (F-L4).


def _require_dev_mode() -> None:
    if os.environ.get("FYJ_DEV") != "1":
        raise HTTPException(status_code=404, detail="Not Found")


@router.post("/api/dev/linkedin/mark-session-valid")
def dev_mark_linkedin_session_valid(request: Request) -> dict[str, Any]:
    """Set `LinkedInSession.status = "valid"` so session-gated UI (the Sync button)
    can be tested without a real login, which a test must never attempt. Writes no
    cookies or storage state, so anything that reaches LinkedIn fails on auth as it
    should; no production code branches on it."""
    _require_dev_mode()
    with _db(request).repos() as repos:
        repos.linkedin_session.update(status="valid", connected_as="Dev Session")
    return {"ok": True, "status": "valid"}


@router.post("/api/dev/browser/navigate")
async def dev_navigate_browser_surface(
    request: Request, payload: dict[str, Any]
) -> dict[str, Any]:
    """Navigate a broker surface server-side, exactly the way an operation
    does (2026-08-16). The user-facing surface went WATCH-ONLY (no URL bar),
    and a surface holds ONE viewer — a test driving it over a second
    screencast socket steals the stream from the page under test. This is the
    e2e stack's navigation lever; same FYJ_DEV gate as every dev tool."""
    _require_dev_mode()
    url = str(payload.get("url") or "")
    if not url:
        raise HTTPException(status_code=422, detail="url is required")
    slug = str(payload.get("surface") or "default")
    broker = getattr(request.app.state, "browser", None)
    if broker is None:
        raise HTTPException(status_code=503, detail="browser broker unavailable")
    surface = await asyncio.to_thread(broker.surface, slug)
    try:
        await surface.wait_ready()
        # Optional geometry, same contract as a viewer's `viewport` message —
        # navigation fails closed without display metrics, and a test driving
        # the surface before (or instead of) a viewer must state its own.
        width, height, dpr = (
            payload.get("width"), payload.get("height"), payload.get("dpr")
        )
        if (
            isinstance(width, int)
            and isinstance(height, int)
            and isinstance(dpr, (int, float))
        ):
            surface.set_geometry(width, height, dpr)
        committed = await asyncio.wait_for(
            asyncio.wrap_future(surface.navigate(url)), timeout=45.0
        )
    except Exception as exc:  # noqa: BLE001 — verbatim to the caller (a dev tool)
        raise HTTPException(
            status_code=502, detail=f"{type(exc).__name__}: {exc}"
        ) from exc
    return {"ok": True, "url": committed}


@router.post("/api/dev/linkedin/seed-search-cursor")
def dev_seed_linkedin_search_cursor(request: Request) -> dict[str, Any]:
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
def dev_expire_linkedin_cookie(request: Request) -> dict[str, Any]:
    """Expire `li_at` in the saved session without touching the session row, so the
    next real LinkedIn action fails on auth mid-flight. Handles a Fernet-sealed or
    legacy plaintext file, resealing in the same format."""
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


def downloads_dir() -> Path:
    """The user's Downloads folder (patched in tests)."""
    return Path.home() / "Downloads"


def export_temp_dir() -> Path:
    """Where an export waits for the shell to copy it to the folder the user
    picks (S-C53). The shell only copies from here (`save_export` in lib.rs)."""
    return Path(tempfile.gettempdir()) / "fyj-exports"


@router.post("/api/export/pdf")
def export_pdf(payload: dto.ExportPdfRequest) -> dto.ExportPdfResult:
    """Render markdown → PDF into ~/Downloads (US-RES-03 slice, 2026-07-12).

    The webview can neither print nor download, so "Export to PDF" posts here;
    the sidecar renders with the same Chromium pipeline the Applier uploads
    (real selectable text) and saves collision-safe. Returns the saved path."""
    from ..registry.pdf import PdfRenderError, render_resume_pdf
    from .browser import is_browser_missing

    if not payload.markdown.strip():
        raise HTTPException(status_code=422, detail="nothing to export — the document is empty")
    stem = re.sub(r"[^\w\- ]+", "", payload.filename).strip().replace(" ", "-") or "document"
    target_dir = export_temp_dir() if payload.destination == "temp" else downloads_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        probe = target_dir / f".fyj-write-check-{os.getpid()}"
        probe.touch()
        probe.unlink()
    except PermissionError as e:
        # macOS asks before an app may use Downloads, and a "Don't Allow"
        # lands here (S-C53).
        raise HTTPException(
            status_code=403,
            detail=(
                f"finds-you-jobs isn't allowed to save in {target_dir}. Allow it under "
                "System Settings › Privacy & Security › Files and Folders › Downloads, "
                "or export from the desktop app, which asks where to save."
            ),
        ) from e
    path = target_dir / f"{stem}.pdf"
    n = 1
    while path.exists() and n < 100:
        path = target_dir / f"{stem}-{n}.pdf"
        n += 1
    try:
        # Sync Playwright refuses to start inside a running asyncio loop (the
        # exact 503 users saw) and would block the loop anyway — a plain `def`
        # handler renders in Starlette's threadpool, like the engine-verify
        # probes.
        render_resume_pdf(payload.markdown, str(path))
    except PdfRenderError as e:
        if is_browser_missing(str(e)):
            # Coded, so the screen offers the Download action (A16).
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "browser_missing",
                    "message": "Chromium isn't downloaded yet; download it, then export again",
                },
            ) from e
        raise HTTPException(status_code=503, detail=str(e)) from e
    return dto.ExportPdfResult(path=str(path))


@router.post("/api/system/install-browser", status_code=202)
def install_browser(request: Request) -> dto.BrowserInstallResult:
    """Download Playwright's Chromium (never bundled — section 4.5). Coarse progress is
    published on the SSE stream as `browser_install` events. Idempotent: a second
    call while one is running returns `already_running`."""
    from .browser import start_install

    hub = getattr(request.app.state, "hub", None)
    publish = hub.publish if hub is not None else None
    status = start_install(publish)
    return dto.BrowserInstallResult(status=status)


@router.get("/api/system/install-browser")
def browser_install_status() -> dto.BrowserInstallStatus:
    """The last Chromium download's state. A client that lost the event stream
    mid-download reads this instead of waiting forever (A21)."""
    from .browser import install_status

    return dto.BrowserInstallStatus(**install_status())


@router.get("/api/system/scheduler")
def scheduler_status(request: Request) -> dto.SchedulerStatus:
    """Whether background work is running, and whether this boot is why it isn't.

    `degraded_boot` means the shell started us with no scheduler because the
    last 3 runs all ended the same bad way. The banner reads this."""
    state = request.app.state
    return dto.SchedulerStatus(
        running=getattr(state, "scheduler", None) is not None,
        degraded_boot=bool(getattr(state, "degraded_boot", False)),
    )


@router.post("/api/system/scheduler/resume")
async def resume_scheduler(request: Request) -> dto.SchedulerStatus:
    """Turn background work back on after a degraded boot. Idempotent.

    `async def` on purpose: starting the tick loop creates an asyncio task, and
    a plain `def` handler runs in Starlette's threadpool where there is no
    running loop to create it on. It touches no database, so nothing blocks."""
    start = getattr(request.app.state, "start_scheduler", None)
    if start is None:
        raise HTTPException(status_code=409, detail="no scheduler on this app")
    start()
    return dto.SchedulerStatus(
        running=getattr(request.app.state, "scheduler", None) is not None,
        degraded_boot=bool(getattr(request.app.state, "degraded_boot", False)),
    )


@router.post("/api/dev/operations/fail-running")
def dev_fail_running(request: Request) -> dict[str, Any]:
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
def dev_seed_queued_operation(request: Request) -> dict[str, Any]:
    """Create a `queued` op row without pumping the runner, so it stays queued and the
    Logs Stop control (F-M7) can be tested deterministically. Kind `cleanup_trash`:
    0 LLM, harmless if dispatched later. Dev-only."""
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
def dev_seed_application(request: Request) -> dict[str, Any]:
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


@router.post("/api/dev/jobs/{job_id}/close")
def dev_close_job(request: Request, job_id: str) -> dict[str, Any]:
    """Close a job the way a scan does when its board stops listing it, so the
    board's "No longer listed" state can be seen without a live board. Dev-only."""
    from ..db.base import now_utc
    from ..registry.persistence import close_job, saved_job_ids

    _require_dev_mode()
    with _db(request).repos() as repos:
        job = repos.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        close_job(repos, job, saved=job_id in saved_job_ids(repos), now=now_utc())
        return {"ok": True, "feed_state": job.feed_state}
