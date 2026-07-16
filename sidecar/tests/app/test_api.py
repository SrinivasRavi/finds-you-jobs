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
