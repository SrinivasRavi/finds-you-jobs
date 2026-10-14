"""Observability (architecture section 10, layer 2: Logfire spans).

`configure_observability` wires Logfire to the local SQLite store (OTLP opt-in);
`operation_span` wraps each op with `record_span_success`/`_failure`
(US-SYS-05); `read_spans_for_operation` serves the drill-down;
`monitor_loop_lag` reports loop stalls. The runner is the only writer; modules
stay framework-free, so engine calls are attributes on the op span."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import logfire

from .loop_lag import LOOP_LAG_INTERVAL_SECONDS, LOOP_LAG_THRESHOLD_SECONDS, monitor_loop_lag
from .setup import (
    DEFAULT_RETENTION_DAYS,
    SPAN_DB_NAME,
    ObservabilityHandle,
    configure_observability,
    reconfigure_observability,
)
from .sqlite_exporter import prune_spans, read_spans_for_operation, span_count

__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "LOOP_LAG_INTERVAL_SECONDS",
    "LOOP_LAG_THRESHOLD_SECONDS",
    "SPAN_DB_NAME",
    "ObservabilityHandle",
    "configure_observability",
    "monitor_loop_lag",
    "operation_span",
    "prune_spans",
    "read_spans_for_operation",
    "reconfigure_observability",
    "record_span_failure",
    "record_span_success",
    "span_count",
]

# Attribute-size fingerprint cap — never store big bodies, only their size, so a
# span can't leak a JD / resume (NFR-SEC-02) unless content logging is opted in.
_INPUT_PREVIEW_MAX = 2000


@contextmanager
def operation_span(
    operation_id: str,
    kind: str,
    *,
    input_snapshot: dict[str, Any] | None = None,
    content_logging: bool = False,
) -> Iterator[Any]:
    """Wrap 1 operation in a Logfire span (US-SYS-05). `operation_id` and `kind`
    always; the snapshot's size always, its content only with content logging on
    (NFR-SEC-02, default off)."""
    with logfire.span("operation", operation_id=operation_id, kind=kind) as span:
        if input_snapshot is not None:
            serialized = json.dumps(input_snapshot, default=str, separators=(",", ":"))
            span.set_attribute("input_bytes", len(serialized))
            if content_logging:
                span.set_attribute("input_snapshot", serialized[:_INPUT_PREVIEW_MAX])
        yield span


def record_span_success(span: Any, outcome: Any) -> None:
    """Stamp the success attributes (engine / model / cost / tokens / latency).

    `outcome` is the runner's `OperationOutcome`; usage is a plain dict (already
    converted from the module dataclass). Missing fields are simply omitted.
    """
    span.set_attribute("outcome", "succeeded")
    if getattr(outcome, "engine", None):
        span.set_attribute("engine", outcome.engine)
    if getattr(outcome, "model", None):
        span.set_attribute("model", outcome.model)
    usage = getattr(outcome, "usage", None) or {}
    _stamp_usage(span, usage)


def _stamp_usage(span: Any, usage: dict[str, Any]) -> None:
    for src, dst in (
        ("usd", "cost_usd"),
        ("latency_ms", "latency_ms"),
        ("tokens_in", "tokens_in"),
        ("tokens_out", "tokens_out"),
        ("internal_calls", "internal_calls"),
        ("model", "model"),
    ):
        value = usage.get(src)
        if value is not None:
            span.set_attribute(dst, value)


def record_span_failure(span: Any, error_message: str, exc: BaseException | None = None) -> None:
    """Mark the span failed with the verbatim error (NFR-SIDE-04's span leg)."""
    span.set_attribute("outcome", "failed")
    span.set_attribute("error", error_message)
    if exc is not None:
        span.record_exception(exc)
    span.set_level("error")
