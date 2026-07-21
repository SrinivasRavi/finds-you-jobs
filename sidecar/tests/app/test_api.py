"""Covers: skeleton — HTTP surface auth + lifecycle routes (architecture §4.2/§4.4).

Uses FastAPI TestClient (sync) against the real app — no mocks of the surface
under test.
"""

from __future__ import annotations

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
