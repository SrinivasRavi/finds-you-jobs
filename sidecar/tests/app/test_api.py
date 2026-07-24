"""Covers: skeleton — HTTP surface auth + lifecycle routes (architecture §4.2/§4.4).

Uses FastAPI TestClient (sync) against the real app — no mocks of the surface
under test.
"""

from __future__ import annotations

from pathlib import Path

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
