"""Covers: core storage — the operations HTTP surface (architecture §4.2/§5.3).

Full-app TestClient (lifespan runs the real migration + runner against a tmp
data dir) with fake operation entrypoints — no mocks of the surface under test.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.registry import OperationContext, OperationOutcome, OperationRegistry

TOKEN = "test-token-abc"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _success(ctx: OperationContext) -> OperationOutcome:
    return OperationOutcome(
        result_ref={"echo": ctx.input_snapshot},
        usage={"usd": 0.01, "tokens_in": 10, "tokens_out": 5},
        engine="fake-engine",
        model="fake-model",
    )


def _boom(ctx: OperationContext) -> OperationOutcome:
    raise ValueError("exact api failure text")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        operation_registry=OperationRegistry({"echo": _success, "boom": _boom}),
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield client


def _wait_for_state(client: TestClient, operation_id: str, target: str) -> dict:
    deadline = time.monotonic() + 5
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/operations/{operation_id}", headers=AUTH).json()
        if body.get("state") == target:
            return body
        time.sleep(0.02)
    raise AssertionError(f"operation never reached {target}: {body}")


def test_operation_post_returns_id_immediately(client: TestClient) -> None:
    resp = client.post("/api/operations/echo", headers=AUTH, json={"n": 1})
    assert resp.status_code == 202
    body = resp.json()
    assert body["kind"] == "echo" and body["state"] == "queued" and body["id"]

    done = _wait_for_state(client, body["id"], "succeeded")
    assert done["result_ref"] == {"echo": {"n": 1}}
    assert done["usage"]["usd"] == 0.01
    assert done["engine"] == "fake-engine"


def test_operation_unknown_kind_404(client: TestClient) -> None:
    resp = client.post("/api/operations/nonsense", headers=AUTH, json={})
    assert resp.status_code == 404


def test_operations_api_requires_token(client: TestClient) -> None:
    assert client.post("/api/operations/echo", json={}).status_code == 401
    assert client.get("/api/operations").status_code == 401


def test_failed_operation_retry_links_old_to_new(client: TestClient) -> None:
    resp = client.post("/api/operations/boom", headers=AUTH, json={"x": 2})
    failed = _wait_for_state(client, resp.json()["id"], "failed")
    assert failed["error"] == "ValueError: exact api failure text"

    retry = client.post(f"/api/operations/{failed['id']}/retry", headers=AUTH)
    assert retry.status_code == 202
    new_id = retry.json()["id"]
    assert new_id != failed["id"]

    # The failed row now carries the durable old→new link.
    old = client.get(f"/api/operations/{failed['id']}", headers=AUTH).json()
    assert old["result_ref"]["retried_as"] == new_id
    _wait_for_state(client, new_id, "failed")  # same kind, same inputs → fails again


def test_list_operations_and_cost_totals(client: TestClient) -> None:
    for n in range(3):
        resp = client.post("/api/operations/echo", headers=AUTH, json={"n": n})
        _wait_for_state(client, resp.json()["id"], "succeeded")

    listed = client.get("/api/operations", headers=AUTH).json()
    assert len(listed) == 3
    assert all(op["kind"] == "echo" for op in listed)

    totals = client.get("/api/cost/totals", headers=AUTH).json()
    assert totals["operations"] == 3
    assert totals["usd"] == pytest.approx(0.03)
    assert totals["by_kind"]["echo"] == pytest.approx(0.03)


def test_sse_events_401_without_token(client: TestClient) -> None:
    # Streaming the infinite SSE generator through TestClient can wedge on
    # close, so only the auth rejection is asserted here; live SSE frames are
    # covered against the real subprocess server in test_integration_boot.
    assert client.get("/api/events").status_code == 401
