"""HTTP surface (architecture §4.2).

Skeleton-commit scope: the lifecycle routes only — /healthz (open) and
/shutdown (bearer-guarded). The jobs/applications/profile/settings CRUD, the
operations API, and the SSE `/api/events` stream land with the core-storage
commit (`docs/internal/roadmap.md` §7.2 #3).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..logging_setup import get_logger

router = APIRouter()


# -- lifecycle -------------------------------------------------------------


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe. Open (no token) — the shell polls this (§4.4 step 2)."""
    return {"status": "ok"}


@router.post("/shutdown")
async def shutdown(request: Request) -> JSONResponse:
    """Respond 200, then exit cleanly (the drain runs in the app lifespan)."""
    get_logger().info("shutdown requested via POST /shutdown")
    request_shutdown = getattr(request.app.state, "request_shutdown", None)
    if request_shutdown is not None:
        request_shutdown()
    return JSONResponse({"status": "shutting_down"}, status_code=200)
