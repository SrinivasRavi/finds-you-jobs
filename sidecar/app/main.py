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
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.discovery import router as discovery_router
from .api.engines import router as engines_router
from .api.ingest import router as ingest_router
from .api.routes import router
from .auth import BearerAuthMiddleware
from .db import Database, resolve_db_url
from .db.base import now_utc
from .db.database import resolve_data_dir
from .db.migrate import upgrade_to_head
from .events import HEARTBEAT_INTERVAL_SECONDS, EventHub
from .logging_setup import get_logger, setup_flight_recorder
from .observability import ObservabilityHandle, configure_observability
from .observability.config import observability_config
from .registry import EngineRegistry, OperationRegistry
from .registry.engine_config import configure_engines
from .registry.operations import backfill_keyword_scores
from .runner import OperationRunner
from .scheduler import Scheduler
from .scheduler.planner import plan_schedule, plan_score_new
from .security import migrate_plaintext_session
from .seed import seed_defaults
from .watchdog import watch_parent

# §4.4 step 3: drain in-flight operations for up to 10 s before force-exit.
SHUTDOWN_DRAIN_SECONDS = 10.0

# The webview loads from tauri://localhost (macOS/Linux) or
# http://tauri.localhost (Windows/Android) in prod; the browser-dev path
# serves from 127.0.0.1:1420. The comment above already documented the prod
# origin, but the regex itself never actually matched it (only http(s)://
# loopback) — every real fetch from a packaged build failed CORS silently,
# invisible until now because no packaged build had ever been run+tested
# (docs/internal/distribution.md §2/§7). Loopback-only by design otherwise
# (§4.2) — this only widens it to the exact schemes Tauri itself uses.
_LOOPBACK_ORIGIN_RE = r"^(https?://(127\.0\.0\.1|localhost)(:\d+)?|tauri://localhost|http://tauri\.localhost)$"


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
            obs_cfg = observability_config(prefs.ui_state)
            engine_rows = repos.engine_settings.list()
        configure_engines(
            engines, routing, engine_rows=engine_rows, data_dir=resolved_data_dir
        )

        # Observability (architecture §10): wire Logfire → local `logfire.sqlite`
        # under the data dir. `send_to_logfire=False` is the hard invariant (no
        # network by default, NFR-OBS-01); OTLP export only when the user opted
        # in via Settings. Failure-tolerant: observability must never block boot,
        # so a bad config degrades to no spans (runner tolerates `None`).
        observability: ObservabilityHandle | None = None
        try:
            observability = configure_observability(
                resolved_data_dir,
                content_logging=obs_cfg.content_logging,
                otlp_enabled=obs_cfg.otlp_enabled,
                otlp_endpoint=obs_cfg.otlp_endpoint,
                otlp_headers=obs_cfg.otlp_headers,
                retention_days=obs_cfg.retention_days,
            )
        except Exception:  # noqa: BLE001 — observability must never block boot
            log.exception("observability configuration failed; continuing without spans")

        # Applier boot recovery (`docs/internal/applier.md` §9.3): an active
        # browser context cannot be silently restored after a restart. Mark
        # orphaned active runs interrupted and cancel their pending ops BEFORE
        # runner boot recovery, so a queued `apply` never relaunches a browser
        # nobody asked for.
        try:
            with db.repos() as repos:
                for run in repos.apply_runs.list_active():
                    repos.apply_runs.update(
                        run.id,
                        status="interrupted",
                        phase="interrupted",
                        summary="app stopped before the browser run completed",
                        ended_at=now_utc(),
                    )
                    if run.operation_id:
                        op = repos.operations.get(run.operation_id)
                        if op is not None and op.state in ("queued", "running"):
                            repos.operations.mark_cancelled(run.operation_id)
        except Exception:  # noqa: BLE001 — recovery must never block boot
            log.exception("apply-run boot recovery failed")

        runner = OperationRunner(
            db,
            registry=operation_registry,
            engines=engines,
            publish=hub.publish,
            observability=observability,
        )

        # Scan → score chain (US-JB-02). Every successful scan (1) gives every
        # still-unscored job an INSTANT on-device keyword floor so it never
        # shows as "Pending" — the board always has at least a grey keyword
        # score (maintainer 2026-07-22), then (2) in AI mode fans out one LLM
        # `score` op per job to upgrade that floor (idempotent planner; capped
        # by thresholds.score_new_batch). Scores land via SSE and re-rank.
        def _chain_scan_to_scores(_operation_id: str, kind: str) -> None:
            if kind != "scan":
                return
            try:
                backfill_keyword_scores(db)
            except Exception:  # noqa: BLE001 — the floor must never break the chain
                log.exception("keyword floor after scan failed")
            for op_kind, snapshot in plan_score_new(db):
                runner.submit(op_kind, snapshot)

        runner.on_success = _chain_scan_to_scores
        runner.start()  # boot recovery (NFR-LONG-02) + first pump

        # Boot keyword floor: existing jobs scored before this landed (or left
        # unscored by a failed/absent LLM) get their grey keyword score now, so
        # no board row is stuck on "Pending" after an update. Off the event
        # loop (async-first rule); never blocks or fails boot.
        def _boot_keyword_floor() -> None:
            try:
                n = backfill_keyword_scores(db)
                if n:
                    log.info("keyword floor: scored %d previously-unscored job(s)", n)
            except Exception:  # noqa: BLE001 — must never hurt boot
                log.exception("boot keyword floor failed")

        threading.Thread(
            target=_boot_keyword_floor, name="keyword-floor", daemon=True
        ).start()

        app.state.db = db
        app.state.hub = hub
        app.state.engines = engines
        app.state.runner = runner
        app.state.observability = observability
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
                watch_parent(
                    original_ppid,
                    _on_orphaned,
                    # Dev spawns via a `uv run` wrapper, so the immediate
                    # parent outlives the shell — the shell passes its own
                    # pid so the watchdog can watch the process that matters.
                    shell_pid=int(os.environ.get("FYJ_SHELL_PID", "0")) or None,
                )
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
        title="finds-you-jobs sidecar", version="0.5.2", lifespan=lifespan
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
    app.include_router(discovery_router)
    app.include_router(ingest_router)
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
