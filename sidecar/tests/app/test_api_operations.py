"""Covers: core storage — the operations HTTP surface (architecture section 4.2/section 5.3).

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


# ---------------------------------------------------------------------------
# POST /api/operations/{id}/cancel — the generic cancel surface (F-M7 route)
# ---------------------------------------------------------------------------


def test_cancel_unknown_operation_is_404(client: TestClient) -> None:
    resp = client.post("/api/operations/nope/cancel", headers=AUTH)
    assert resp.status_code == 404


def test_cancel_terminal_operation_is_409(client: TestClient) -> None:
    op_id = client.post("/api/operations/echo", headers=AUTH, json={}).json()["id"]
    _wait_for_state(client, op_id, "succeeded")
    resp = client.post(f"/api/operations/{op_id}/cancel", headers=AUTH)
    assert resp.status_code == 409


def test_cancel_queued_operation_is_202_and_lands_cancelled(client: TestClient) -> None:
    # Create the row directly (no runner pump) so it is still `queued` when the
    # cancel arrives — the route answers 202 with the OperationAccepted DTO
    # (same shape as retry) and the op must land `cancelled`.
    db = client.app.state.db  # type: ignore[attr-defined]
    with db.repos() as repos:
        op_id = repos.operations.create("echo", {}).id
    resp = client.post(f"/api/operations/{op_id}/cancel", headers=AUTH)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body == {"id": op_id, "kind": "echo", "state": "cancelled"}
    fetched = client.get(f"/api/operations/{op_id}", headers=AUTH).json()
    assert fetched["state"] == "cancelled"


def test_cancel_running_non_polling_kind_is_409(tmp_path: Path) -> None:
    """Honesty (2026-07-25): a RUNNING kind that never observes the cancel
    token (scan/discover/send/apply — anything outside
    CANCELLABLE_RUNNING_KINDS) is refused with 409, not a 202 that cancels
    nothing. Queued ops of the same kind stay cancellable (test above)."""
    import threading

    started = threading.Event()
    release = threading.Event()

    def _blocking(ctx: OperationContext) -> OperationOutcome:
        started.set()
        release.wait(timeout=5)
        return OperationOutcome()

    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        operation_registry=OperationRegistry({"scan": _blocking}),
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        op_id = client.post("/api/operations/scan", headers=AUTH, json={}).json()["id"]
        assert started.wait(timeout=5)
        resp = client.post(f"/api/operations/{op_id}/cancel", headers=AUTH)
        assert resp.status_code == 409
        release.set()
        _wait_for_state(client, op_id, "succeeded")


def test_cancel_running_polling_kind_is_202_and_lands_cancelled(tmp_path: Path) -> None:
    """A RUNNING kind that polls the token (score/tailor/cover) is genuinely
    cancellable: 202 with the honest post-cancel state (`running` until the
    entrypoint's next checkpoint), then the op lands `cancelled`."""
    import threading

    from sidecar.modules._shared.completion_retry import CompletionCancelled

    started = threading.Event()

    def _cooperative(ctx: OperationContext) -> OperationOutcome:
        assert ctx.cancelled is not None
        started.set()
        deadline = time.monotonic() + 5
        while not ctx.cancelled():
            if time.monotonic() > deadline:
                raise AssertionError("cancel request never reached the worker")
            time.sleep(0.01)
        raise CompletionCancelled("operation cancelled by the user")

    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        operation_registry=OperationRegistry({"score": _cooperative}),
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        op_id = client.post("/api/operations/score", headers=AUTH, json={}).json()["id"]
        assert started.wait(timeout=5)
        resp = client.post(f"/api/operations/{op_id}/cancel", headers=AUTH)
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["id"] == op_id and body["kind"] == "score"
        assert body["state"] in ("running", "cancelled")  # honest — not yet terminal
        _wait_for_state(client, op_id, "cancelled")
