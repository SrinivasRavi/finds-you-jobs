"""Covers: A6 Observability (US-SYS-05, FR-SYS-05, NFR-SIDE-04, NFR-OBS-01/02).

Three planes:

1. The local SQLite span store (`SqliteSpanExporter`) — export, per-operation
   read, retention prune. No network, ever.
2. The `ui_state` → observability-config parser (defaults are the safe,
   no-network baseline).
3. The runner emits a per-operation Logfire span with the US-SYS-05 attribute
   list; a failure lands in **all three legs** — operations row + span + SSE
   event (NFR-SIDE-04, the explicit three-legged assertion).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import logfire
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from sidecar.app.db import Database
from sidecar.app.observability import (
    configure_observability,
    operation_span,
    read_spans_for_operation,
    record_span_failure,
    record_span_success,
    span_count,
)
from sidecar.app.observability.config import observability_config
from sidecar.app.observability.setup import reconfigure_observability
from sidecar.app.observability.sqlite_exporter import (
    SqliteSpanExporter,
    prune_spans,
)
from sidecar.app.registry import OperationContext, OperationOutcome, OperationRegistry
from sidecar.app.runner import OperationRunner

from .conftest import wait_for_state

# ---------------------------------------------------------------------------
# 1. The SQLite span store — a self-contained OTel provider (no logfire global)
# ---------------------------------------------------------------------------


def _emit_span(exporter: SqliteSpanExporter, **attrs: Any) -> None:
    """Emit one finished span through a private provider (test isolation)."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("operation") as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)
    provider.force_flush()


def test_exporter_persists_and_reads_by_operation(tmp_path: Path) -> None:
    path = tmp_path / "logfire.sqlite"
    exporter = SqliteSpanExporter(path)
    _emit_span(exporter, operation_id="op-A", kind="scan", cost_usd=0.0, outcome="succeeded")
    _emit_span(exporter, operation_id="op-B", kind="score", outcome="succeeded")

    rows = read_spans_for_operation(path, "op-A")
    assert len(rows) == 1
    row = rows[0]
    assert row["operation_id"] == "op-A"
    assert row["op_kind"] == "scan"
    assert row["attributes"]["outcome"] == "succeeded"
    assert row["duration_ms"] >= 0
    assert span_count(path) == 2
    assert read_spans_for_operation(path, "nope") == []


def test_read_missing_store_is_empty(tmp_path: Path) -> None:
    # No store file yet → empty, never an error (the drill-down only enriches).
    assert read_spans_for_operation(tmp_path / "absent.sqlite", "x") == []
    assert span_count(tmp_path / "absent.sqlite") == 0


def test_prune_drops_only_old_spans(tmp_path: Path) -> None:
    path = tmp_path / "logfire.sqlite"
    exporter = SqliteSpanExporter(path)
    _emit_span(exporter, operation_id="recent", kind="scan")
    assert span_count(path) == 1
    # Nothing older than 30 days yet.
    assert prune_spans(path, older_than_days=30) == 0
    # Cutoff in the far future → everything is "old".
    future_ns = time.time_ns() + 40 * 86_400 * 1_000_000_000
    assert prune_spans(path, older_than_days=30, now_ns=future_ns) == 1
    assert span_count(path) == 0


# ---------------------------------------------------------------------------
# 2. ui_state → observability config parser
# ---------------------------------------------------------------------------


def test_config_defaults_are_no_network() -> None:
    cfg = observability_config(None)
    assert cfg.content_logging is False
    assert cfg.otlp_enabled is False
    assert cfg.otlp_endpoint == ""
    assert cfg.retention_days == 30


def test_config_reads_opt_in_block() -> None:
    cfg = observability_config(
        {
            "content_logging": True,
            "otlp_enabled": True,
            "otlp_endpoint": "https://otlp.example.com:4318",
            "otlp_headers": {"authorization": "Bearer x"},
            "retention_days": 7,
        }
    )
    assert cfg.content_logging is True
    assert cfg.otlp_enabled is True
    assert cfg.otlp_endpoint == "https://otlp.example.com:4318"
    assert cfg.otlp_headers == {"authorization": "Bearer x"}
    assert cfg.retention_days == 7


def test_config_tolerates_garbage_retention() -> None:
    cfg = observability_config({"retention_days": "not-a-number"})
    assert cfg.retention_days == 30


# ---------------------------------------------------------------------------
# 3. configure_observability + the span helpers (via the global logfire)
# ---------------------------------------------------------------------------


def test_configure_defaults_no_otlp(tmp_path: Path) -> None:
    handle = configure_observability(tmp_path)
    assert handle.otlp_enabled is False  # OFF ⇒ no exporter (the hard invariant)
    assert handle.content_logging is False
    assert handle.span_db_path == tmp_path / "logfire.sqlite"
    assert handle.span_db_path.exists()  # store initialized, zero network


def test_configure_otlp_opt_in_marks_enabled(tmp_path: Path) -> None:
    # A syntactically-valid endpoint: the exporter builds but never connects in
    # this test (no span flushed to it) — we assert the opt-in took effect.
    handle = configure_observability(
        tmp_path, otlp_enabled=True, otlp_endpoint="http://127.0.0.1:4318/v1/traces"
    )
    assert handle.otlp_enabled is True
    # Empty endpoint ⇒ stays OFF even when the flag is true.
    handle2 = configure_observability(tmp_path, otlp_enabled=True, otlp_endpoint="   ")
    assert handle2.otlp_enabled is False


def test_reconfigure_toggles_in_place(tmp_path: Path) -> None:
    handle = configure_observability(tmp_path)
    assert handle.content_logging is False
    reconfigure_observability(
        handle,
        tmp_path,
        content_logging=True,
        otlp_enabled=False,
        otlp_endpoint="",
        otlp_headers=None,
        retention_days=30,
    )
    # Same object, mutated — the runner's reference now sees content_logging on.
    assert handle.content_logging is True


def test_operation_span_omits_content_by_default(tmp_path: Path) -> None:
    handle = configure_observability(tmp_path)
    with operation_span(
        "op-content", "score", input_snapshot={"job": "SECRET JD TEXT"}, content_logging=False
    ) as span:
        record_span_success(
            span, OperationOutcome(usage={"usd": 0.1, "model": "m"}, engine="e", model="m")
        )
    logfire.force_flush()
    row = read_spans_for_operation(handle.span_db_path, "op-content")[0]
    attrs = row["attributes"]
    assert "input_bytes" in attrs  # size recorded
    assert "input_snapshot" not in attrs  # but NOT the content (NFR-SEC-02)
    assert attrs["cost_usd"] == 0.1
    assert attrs["outcome"] == "succeeded"


def test_operation_span_includes_content_when_opted_in(tmp_path: Path) -> None:
    handle = configure_observability(tmp_path, content_logging=True)
    with operation_span(
        "op-optin", "score", input_snapshot={"job": "VISIBLE"}, content_logging=True
    ) as span:
        record_span_failure(span, "ValueError: boom")
    logfire.force_flush()
    row = read_spans_for_operation(handle.span_db_path, "op-optin")[0]
    assert "VISIBLE" in row["attributes"]["input_snapshot"]


# ---------------------------------------------------------------------------
# 4. The runner emits spans + the NFR-SIDE-04 three-legged failure guarantee
# ---------------------------------------------------------------------------


def _success(ctx: OperationContext) -> OperationOutcome:
    return OperationOutcome(
        result_ref={"ok": True},
        usage={"internal_calls": 1, "usd": 0.03, "tokens_in": 10, "tokens_out": 5, "model": "m"},
        engine="fake-engine",
        model="m",
    )


def _boom(ctx: OperationContext) -> OperationOutcome:
    raise ValueError("exact failure text 42")


@pytest.fixture
def events() -> list[dict[str, Any]]:
    return []


def _runner(db: Database, tmp_path: Path, events: list[dict[str, Any]]) -> OperationRunner:
    handle = configure_observability(tmp_path)
    registry = OperationRegistry({"scan": _success, "score": _boom})
    return OperationRunner(
        db,
        registry=registry,
        publish=events.append,
        observability=handle,
    )


def test_runner_success_emits_span_with_attributes(
    migrated_db: Database, tmp_path: Path, events: list[dict[str, Any]]
) -> None:
    runner = _runner(migrated_db, tmp_path, events)
    runner.start()
    try:
        op_id = runner.submit("scan", {"portals_config": {}})
        wait_for_state(migrated_db, op_id, "succeeded")
    finally:
        runner.shutdown()
    logfire.force_flush()

    spans = read_spans_for_operation(runner._observability.span_db_path, op_id)  # type: ignore[union-attr]
    assert len(spans) == 1
    attrs = spans[0]["attributes"]
    # US-SYS-05 attribute list: id / kind / engine / model / cost / latency / outcome.
    assert attrs["operation_id"] == op_id
    assert attrs["kind"] == "scan"
    assert attrs["engine"] == "fake-engine"
    assert attrs["model"] == "m"
    assert attrs["cost_usd"] == 0.03
    assert attrs["tokens_in"] == 10
    assert attrs["outcome"] == "succeeded"
    assert spans[0]["status"] in ("OK", "UNSET")


def test_failure_lands_in_row_span_and_event(
    migrated_db: Database, tmp_path: Path, events: list[dict[str, Any]]
) -> None:
    """NFR-SIDE-04: a failed op → (a) row error, (b) span error, (c) SSE event."""
    runner = _runner(migrated_db, tmp_path, events)
    runner.start()
    try:
        op_id = runner.submit("score", {})
        wait_for_state(migrated_db, op_id, "failed")
    finally:
        runner.shutdown()
    logfire.force_flush()

    message = "ValueError: exact failure text 42"

    # (a) the operations row carries the verbatim error.
    with migrated_db.repos() as repos:
        op = repos.operations.get(op_id)
        assert op is not None
        assert op.state == "failed"
        assert op.error == message

    # (b) a span with error attributes + ERROR status + the exception event.
    spans = read_spans_for_operation(runner._observability.span_db_path, op_id)  # type: ignore[union-attr]
    assert len(spans) == 1
    span = spans[0]
    assert span["status"] == "ERROR"
    assert span["attributes"]["outcome"] == "failed"
    assert span["attributes"]["error"] == message
    assert any(ev["name"] == "exception" for ev in span["events"])

    # (c) an SSE 'operation' event with the failed state + verbatim error.
    failed_events = [
        e
        for e in events
        if e["type"] == "operation"
        and e["payload"]["id"] == op_id
        and e["payload"]["state"] == "failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["payload"]["error"] == message
