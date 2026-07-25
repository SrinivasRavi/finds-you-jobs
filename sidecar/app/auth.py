"""Bearer-token auth for the loopback API (architecture §4.2).

Every request carries `Authorization: Bearer <token>` except `/healthz` (open,
so the shell's health poll needs no secret). The SSE stream additionally accepts
the token as a `?token=` query param — acceptable strictly because the surface
is 127.0.0.1-only (NFR-SEC-03).
"""

from __future__ import annotations

import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

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


class BearerAuthMiddleware:
    """Rejects any non-open request lacking a valid bearer token with 401.

    Pure ASGI on purpose (F-M10): BaseHTTPMiddleware wraps every response in a
    per-request task group that buffers streams and has documented quirks around
    client-disconnect propagation — wrapping the app-lifetime `/api/events` SSE
    stream in it could delay unsubscribe after the webview drops. Same reason
    `main.py`'s flight-recorder middleware is pure ASGI."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self.app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Request(scope) is a cheap view over the scope (no body access) — it
        # keeps header/query parsing identical to the old Starlette path.
        request = Request(scope)
        if request.url.path in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        if not token_ok(extract_token(request), self._token):
            response = JSONResponse(
                {"detail": "missing or invalid bearer token"}, status_code=401
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
