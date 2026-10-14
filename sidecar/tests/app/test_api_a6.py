"""Covers: A6 observability over HTTP (US-SYS-05, US-SET-09, FR-SET-08).

Through the real app: `GET /api/operations/{id}/spans` returns an op's spans,
including a failed op's error span; observability settings round-trip through
/api/settings and default to no network. `score` with an empty snapshot fails
before any engine call, a real span at 0 LLM spend."""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._support import AUTH


def _poll(client: TestClient, op_id: str, target: str, timeout: float = 4.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/operations/{op_id}", headers=AUTH).json()
        if last["state"] == target:
            return last
        time.sleep(0.03)
    raise AssertionError(f"operation never reached {target}: {last}")


def _poll_spans(client: TestClient, op_id: str, timeout: float = 4.0) -> list[dict]:
    """Read an op's spans, retrying until 1 appears: the span exports just after the
    row turns terminal, so an immediate read can race it."""
    deadline = time.monotonic() + timeout
    spans: list[dict] = []
    while time.monotonic() < deadline:
        spans = client.get(f"/api/operations/{op_id}/spans", headers=AUTH).json()
        if spans:
            return spans
        time.sleep(0.03)
    return spans


# -- spans drill-down ------------------------------------------------------


def test_spans_endpoint_requires_token(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    assert client.get("/api/operations/whatever/spans").status_code == 401


def test_failed_op_exposes_error_span(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    op_id = client.post("/api/operations/score", json={}, headers=AUTH).json()["id"]
    row = _poll(client, op_id, "failed")
    assert row["error"]  # verbatim on the row (leg a)

    spans = _poll_spans(client, op_id)
    assert len(spans) == 1
    span = spans[0]
    assert span["operation_id"] == op_id
    assert span["op_kind"] == "score"
    assert span["status"] == "ERROR"
    assert span["attributes"]["outcome"] == "failed"
    # The span's verbatim error matches the operations row (leg b == leg a).
    assert span["attributes"]["error"] == row["error"]
    assert any(ev["name"] == "exception" for ev in span["events"])


def test_spans_unknown_operation_is_empty(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.get("/api/operations/does-not-exist/spans", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


# -- observability settings round-trip -------------------------------------


def test_observability_defaults_off(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    ui = client.get("/api/settings", headers=AUTH).json()["preferences"]["ui_state"]
    # Default baseline: no content logging, no OTLP export.
    assert ui.get("content_logging", False) is False
    assert ui.get("otlp_enabled", False) is False


def test_otlp_opt_in_round_trips(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    body = {
        "ui_state": {
            "content_logging": True,
            "otlp_enabled": True,
            "otlp_endpoint": "http://127.0.0.1:4318/v1/traces",
            "retention_days": 14,
        }
    }
    resp = client.post("/api/settings", json=body, headers=AUTH)
    assert resp.status_code == 200
    ui = resp.json()["preferences"]["ui_state"]
    assert ui["otlp_enabled"] is True
    assert ui["otlp_endpoint"] == "http://127.0.0.1:4318/v1/traces"
    # The live handle was reconfigured in place (content logging now on).
    assert app.state.observability.content_logging is True
    assert app.state.observability.otlp_enabled is True


def test_otlp_can_be_turned_back_off(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    client.post(
        "/api/settings",
        json={"ui_state": {"otlp_enabled": True, "otlp_endpoint": "http://127.0.0.1:4318"}},
        headers=AUTH,
    )
    assert app.state.observability.otlp_enabled is True
    # Turn it off → no exporter at all (the hard invariant).
    client.post("/api/settings", json={"ui_state": {"otlp_enabled": False}}, headers=AUTH)
    assert app.state.observability.otlp_enabled is False
