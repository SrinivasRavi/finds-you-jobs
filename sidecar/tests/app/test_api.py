"""Covers: skeleton — HTTP surface auth + lifecycle routes (architecture §4.2/§4.4).

Uses FastAPI TestClient (sync) against the real app — no mocks of the surface
under test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sidecar.app.main import create_app

TOKEN = "test-token-abc"  # noqa: S105 — test fixture, not a real secret


@pytest.fixture
def client() -> TestClient:
    # original_ppid=None → watchdog off, so the test app doesn't self-shutdown.
    return TestClient(create_app(token=TOKEN, original_ppid=None))


def test_healthz_open_no_token(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_protected_route_401_without_token(client: TestClient) -> None:
    # /shutdown is protected; no Authorization header → 401.
    resp = client.post("/shutdown")
    assert resp.status_code == 401


def test_protected_route_401_with_wrong_token(client: TestClient) -> None:
    resp = client.post("/shutdown", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_protected_route_200_with_token(client: TestClient) -> None:
    resp = client.post("/shutdown", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "shutting_down"}


def test_shutdown_invokes_hook() -> None:
    fired = {"value": False}
    app = create_app(token=TOKEN, original_ppid=None)
    app.state.request_shutdown = lambda: fired.__setitem__("value", True)
    with TestClient(app) as client:
        resp = client.post("/shutdown", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert fired["value"] is True


# Regression coverage for a packaged-build-only bug (distribution.md §2/§7):
# every real fetch from the packaged app's actual webview origin was silently
# rejected by CORS, because the origin regex only ever matched http(s)://
# loopback — never the tauri://localhost / http://tauri.localhost origins the
# packaged webview actually loads from. Invisible until the first packaged
# build was ever run, since dev's origin (http://localhost:1420) did match.
@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:1420",  # browser-dev (Vite)
        "tauri://localhost",  # packaged webview: macOS / Linux
        "http://tauri.localhost",  # packaged webview: Windows / Android
    ],
)
def test_cors_preflight_allows_real_webview_origins(client: TestClient, origin: str) -> None:
    resp = client.options(
        "/healthz",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


def test_cors_preflight_rejects_other_origins(client: TestClient) -> None:
    resp = client.options(
        "/healthz",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


def test_unhandled_route_error_is_logged_to_flight_recorder(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """An unexpected route crash must never go unlogged (2026-07-24): the
    log-and-reraise middleware writes the traceback to the flight recorder
    while leaving the 500 propagation exactly as before."""
    import logging

    from sidecar.app.logging_setup import LOGGER_NAME

    # No lifespan on purpose (client used without `with`, matching the module
    # fixture): the middleware is pure ASGI and needs no runner/DB — and a
    # lifespan-booted app here proved able to wedge the browser-driven apply
    # tests that run later in the same process. Isolated data_dir keeps the
    # flight-recorder file out of the developer's real app-data either way.
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )

    @app.get("/api/_test_boom")
    async def _boom() -> None:
        raise RuntimeError("deliberate test crash")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        resp = client.get(
            "/api/_test_boom", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert resp.status_code == 500
    assert any(
        "unhandled error on GET /api/_test_boom" in r.message for r in caplog.records
    )


# ─── F-M10: BearerAuthMiddleware is pure ASGI — behavior preserved exactly ───
# The middleware no longer rides on BaseHTTPMiddleware (whose per-request task
# group buffers streams and can delay SSE unsubscribe on webview drop). These
# tests pin every behavior of the old dispatch at the ASGI level: the /healthz
# exemption, header tokens, the `?token=` allowance for the SSE stream and
# screenshot GETs (and ONLY those), the 401 shape, and non-http pass-through.


def _asgi_scope(path: str, query: bytes = b"", headers: list | None = None) -> dict:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "server": ("127.0.0.1", 80),
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": headers or [],
    }


async def _run_middleware(scope: dict) -> tuple[bool, list[Any]]:
    """Drive BearerAuthMiddleware over a recording inner app. Returns
    (inner_app_reached, messages sent to the client)."""
    from starlette.types import Message, Receive, Scope, Send

    from sidecar.app.auth import BearerAuthMiddleware

    reached = {"value": False}

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        reached["value"] = True
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"ok"})

    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await BearerAuthMiddleware(inner, token=TOKEN)(scope, receive, send)
    return reached["value"], sent


async def test_asgi_auth_healthz_open() -> None:
    reached, _ = await _run_middleware(_asgi_scope("/healthz"))
    assert reached is True


async def test_asgi_auth_header_token_accepted() -> None:
    headers = [(b"authorization", f"Bearer {TOKEN}".encode())]
    reached, _ = await _run_middleware(_asgi_scope("/api/jobs", headers=headers))
    assert reached is True


async def test_asgi_auth_missing_token_is_401_with_detail() -> None:
    reached, sent = await _run_middleware(_asgi_scope("/api/jobs"))
    assert reached is False
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 401
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    import json as _json

    assert _json.loads(body) == {"detail": "missing or invalid bearer token"}


async def test_asgi_auth_query_token_allowed_for_sse_stream() -> None:
    reached, _ = await _run_middleware(
        _asgi_scope("/api/events", query=f"token={TOKEN}".encode())
    )
    assert reached is True


async def test_asgi_auth_query_token_allowed_for_screenshot_get() -> None:
    reached, _ = await _run_middleware(
        _asgi_scope("/api/apply-runs/r1/screenshot", query=f"token={TOKEN}".encode())
    )
    assert reached is True


async def test_asgi_auth_query_token_rejected_elsewhere() -> None:
    # `?token=` only rides on the SSE stream + screenshot GETs (NFR-SEC-03) —
    # any other path must still 401 without the header.
    reached, sent = await _run_middleware(
        _asgi_scope("/api/jobs", query=f"token={TOKEN}".encode())
    )
    assert reached is False
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 401


async def test_asgi_auth_non_http_scope_passes_through() -> None:
    from starlette.types import Receive, Scope, Send

    from sidecar.app.auth import BearerAuthMiddleware

    seen = {"value": False}

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen["value"] = True

    await BearerAuthMiddleware(inner, token=TOKEN)(
        {"type": "lifespan"},
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )
    assert seen["value"] is True
