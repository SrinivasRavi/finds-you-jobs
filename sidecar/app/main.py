"""FastAPI app factory (architecture §4.1 `app/api`).

Skeleton-commit scope: bearer auth, loopback CORS, flight-recorder log, orphan
watchdog, and the lifecycle routes (/healthz, /shutdown). The SQLite storage,
SSE event hub, Operation Runner, and scheduler land in the core-storage commit
(`docs/internal/roadmap.md` §7.2 #3) and come up in this same lifespan.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .auth import BearerAuthMiddleware
from .logging_setup import get_logger, setup_flight_recorder
from .watchdog import watch_parent

# §4.4 step 3: drain in-flight operations for up to 10 s before force-exit.
SHUTDOWN_DRAIN_SECONDS = 10.0

# The webview loads from tauri://localhost (prod) / a locked CSP; the
# browser-dev path serves from 127.0.0.1:1420. Loopback-only regex — the whole
# surface is 127.0.0.1 by design (§4.2).
_LOOPBACK_ORIGIN_RE = r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$"


def create_app(
    *,
    token: str,
    original_ppid: int | None = None,
) -> FastAPI:
    """Build the sidecar FastAPI app.

    `token` guards every non-open route. `original_ppid` (the shell's pid at
    spawn) arms the orphan watchdog; when None (tests, standalone curl) the
    watchdog is skipped.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_flight_recorder()
        log = get_logger()
        log.info("sidecar app starting (original_ppid=%s)", original_ppid)

        watchdog_task: asyncio.Task[None] | None = None
        if original_ppid is not None:

            async def _on_orphaned() -> None:
                request_shutdown = getattr(app.state, "request_shutdown", None)
                if request_shutdown is not None:
                    request_shutdown()

            watchdog_task = asyncio.create_task(
                watch_parent(original_ppid, _on_orphaned)
            )

        try:
            yield
        finally:
            if watchdog_task is not None:
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except asyncio.CancelledError:
                    pass
            log.info("sidecar app stopped")

    app = FastAPI(
        title="finds-you-jobs sidecar", version="0.1.0-dev", lifespan=lifespan
    )

    app.state.token = token
    app.state.original_ppid = original_ppid
    # __main__ assigns this to flip the uvicorn server's should_exit. Left None
    # under TestClient (no server) — /shutdown then simply 200s.
    app.state.request_shutdown = None

    # Auth added first, CORS last → CORS is the outermost layer so preflight
    # OPTIONS resolve before the token check.
    app.add_middleware(BearerAuthMiddleware, token=token)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_LOOPBACK_ORIGIN_RE,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


def app_from_env() -> FastAPI:
    """Factory for `uvicorn sidecar.app.main:app_from_env` style runs.

    Reads FYJ_API_TOKEN / FYJ_ORIGINAL_PPID. `__main__` uses the direct
    `create_app` path; this exists for ad-hoc reload-mode runs.
    """
    token = os.environ.get("FYJ_API_TOKEN", "")
    ppid_env = os.environ.get("FYJ_ORIGINAL_PPID")
    original_ppid = int(ppid_env) if ppid_env else None
    return create_app(token=token, original_ppid=original_ppid)
