"""FastAPI app factory (architecture §4.1 `app/api`).

Wires the scaffold (bearer auth, loopback CORS, flight-recorder log, orphan
watchdog, lifecycle routes) plus the core-storage slice
(`docs/internal/roadmap.md` §7.2 #3): the SQLite storage + migrations, the SSE
event hub, and the Operation Runner. The runner comes up in the lifespan and
drains on shutdown. Engine routing and the scheduler land with their feature
commits in this same lifespan.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.engines import router as engines_router
from .api.routes import router
from .auth import BearerAuthMiddleware
from .db import Database, resolve_db_url
from .db.database import resolve_data_dir
from .db.migrate import upgrade_to_head
from .events import HEARTBEAT_INTERVAL_SECONDS, EventHub
from .logging_setup import get_logger, setup_flight_recorder
from .registry import EngineRegistry, OperationRegistry
from .registry.engine_config import configure_engines
from .runner import OperationRunner
from .scheduler import Scheduler
from .scheduler.planner import plan_schedule, plan_score_new
from .security import migrate_plaintext_session
from .seed import seed_defaults
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
    data_dir: str | os.PathLike[str] | None = None,
    operation_registry: OperationRegistry | None = None,
    enable_scheduler: bool = True,
) -> FastAPI:
    """Build the sidecar FastAPI app.

    `token` guards every non-open route. `original_ppid` (the shell's pid at
    spawn) arms the orphan watchdog; when None (tests, standalone curl) the
    watchdog is skipped. `data_dir` overrides the DB/app-data location (tests
    point it at a tmp dir); None falls back to FYJ_DATA_DIR / the platform dir.
    `operation_registry` overrides the default kind table (tests register fake
    entrypoints); None uses `default_operation_registry()`. `enable_scheduler`
    runs the 60 s tick loop (off for isolated route tests).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_flight_recorder()
        log = get_logger()
        log.info("sidecar app starting (original_ppid=%s)", original_ppid)

        # Storage: migrate to head, then open the engine (architecture §5.3 boot).
        db_url = resolve_db_url(data_dir)
        upgrade_to_head(db_url)
        # Alembic's fileConfig (inside upgrade_to_head) disables existing loggers,
        # including our flight recorder — re-arm it so every runner/operation log
        # written after boot lands in the file (was the "only boot lines land" bug).
        # setup_flight_recorder is idempotent and clears `.disabled`.
        setup_flight_recorder()
        db = Database(db_url)
        seed_defaults(db)  # first-run portals config + (disabled) schedules
        # NFR-SEC-01: seal a pre-encryption plaintext LinkedIn session file if one
        # exists (roundtrip-verified, atomic; no-op when absent/sealed — and it
        # never touches the OS keychain unless a file is present).
        try:
            if migrate_plaintext_session(resolve_data_dir(data_dir)):
                log.info("LinkedIn session file migrated to encrypted-at-rest")
        except Exception:  # noqa: BLE001 — migration must never block boot
            log.exception("LinkedIn session encrypt-at-rest migration failed")
        hub = EventHub()
        hub.bind_loop(asyncio.get_running_loop())

        # Register the claude-cli engine + any configured BYOK engines from the
        # persisted EngineSettings rows, then apply the settings routing map.
        engines = EngineRegistry()
        resolved_data_dir = resolve_data_dir(data_dir)
        with db.repos() as repos:
            prefs = repos.preferences.get_or_create()
            routing = prefs.engine_routing
            engine_rows = repos.engine_settings.list()
        configure_engines(
            engines, routing, engine_rows=engine_rows, data_dir=resolved_data_dir
        )

        runner = OperationRunner(
            db, registry=operation_registry, engines=engines, publish=hub.publish
        )

        # Scan → score chain (US-JB-02): every successful scan fans out one
        # `score` op per unscored job (idempotent planner — cache + pending
        # skipped; capped by thresholds.score_new_batch; no-op without a master
        # profile). Scores land one by one and the board re-ranks per SSE event.
        def _chain_scan_to_scores(_operation_id: str, kind: str) -> None:
            if kind != "scan":
                return
            for op_kind, snapshot in plan_score_new(db):
                runner.submit(op_kind, snapshot)

        runner.on_success = _chain_scan_to_scores
        runner.start()  # boot recovery (NFR-LONG-02) + first pump

        app.state.db = db
        app.state.hub = hub
        app.state.engines = engines
        app.state.runner = runner
        app.state.data_dir = resolved_data_dir

        scheduler: Scheduler | None = None
        scheduler_task: asyncio.Task[None] | None = None
        if enable_scheduler:
            scheduler = Scheduler(
                db,
                runner,
                planner=lambda kind: plan_schedule(db, kind),
                publish=hub.publish,
            )
            scheduler_task = asyncio.create_task(scheduler.run_forever())
        app.state.scheduler = scheduler

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
            if scheduler is not None:
                scheduler.stop()
            for task in (scheduler_task, watchdog_task):
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            # Drain in-flight operations, then release the DB engine (§4.4 step 3).
            runner.shutdown(drain_timeout=SHUTDOWN_DRAIN_SECONDS)
            db.dispose()
            log.info("sidecar app stopped")

    app = FastAPI(
        title="finds-you-jobs sidecar", version="0.1.0-dev", lifespan=lifespan
    )

    app.state.token = token
    app.state.original_ppid = original_ppid
    # __main__ assigns this to flip the uvicorn server's should_exit. Left None
    # under TestClient (no server) — /shutdown then simply 200s.
    app.state.request_shutdown = None
    app.state.heartbeat_interval = HEARTBEAT_INTERVAL_SECONDS

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
    app.include_router(engines_router)
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
