"""HTTP surface (architecture §4.2).

Core-storage scope (`docs/internal/roadmap.md` §7.2 #3): the lifecycle routes
(/healthz open, /shutdown bearer-guarded), the SSE `/api/events` stream fed by
the runner through the hub, and the operations API — enqueue, read, list,
retry, and the all-time cost totals. Pydantic DTOs (dto.py) are the only wire
types; ORM never crosses this line. The jobs/applications/profile/settings
CRUD lands with its feature commits.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..db import Database
from ..events import heartbeat_stream
from ..logging_setup import get_logger
from ..runner import OperationRunner
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
