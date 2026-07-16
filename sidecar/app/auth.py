"""Bearer-token auth for the loopback API (architecture §4.2).

Every request carries `Authorization: Bearer <token>` except `/healthz` (open,
so the shell's health poll needs no secret). The SSE stream additionally accepts
the token as a `?token=` query param — acceptable strictly because the surface
is 127.0.0.1-only (NFR-SEC-03).
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

# Paths reachable without a token.
OPEN_PATHS = frozenset({"/healthz"})
# Paths that may present the token via `?token=` (SSE can't set headers easily).
QUERY_TOKEN_PATHS = frozenset({"/api/events"})

_BEARER_PREFIX = "Bearer "


def _allows_query_token(path: str) -> bool:
    """True where the token may ride in `?token=` instead of a header: the SSE
    stream, and screenshot GETs an <img src> loads directly (A5b). Loopback-only
    surface, so a query-param token is acceptable (NFR-SEC-03)."""
    return path in QUERY_TOKEN_PATHS or path.endswith("/screenshot")


def extract_token(request: Request) -> str | None:
    """Pull the presented token from the Authorization header or, where allowed,
    the `token` query param. Returns None if absent."""
    header = request.headers.get("authorization")
    if header and header.startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX) :].strip()
    if _allows_query_token(request.url.path):
        qp = request.query_params.get("token")
        if qp:
            return qp
    return None


def token_ok(presented: str | None, expected: str) -> bool:
    """Constant-time compare. False on a missing/empty presented token."""
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Rejects any non-open request lacking a valid bearer token with 401."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        if not token_ok(extract_token(request), self._token):
            return JSONResponse(
                {"detail": "missing or invalid bearer token"}, status_code=401
            )
        return await call_next(request)
