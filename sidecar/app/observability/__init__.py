"""Observability (architecture §10, layer 2 — Logfire tracing spans).

Public surface:

- `configure_observability` / `ObservabilityHandle` — wire Logfire to the local
  SQLite span store (+ opt-in OTLP). Called once from the app lifespan.
- `operation_span` — the context manager the runner wraps each operation in.
- `record_span_success` / `record_span_failure` — set the US-SYS-05 attribute
  list (engine / model / cost / latency / outcome) on that span.
- `read_spans_for_operation` — the Logs drill-down read path.

The runner is the only writer of operation spans; everything else reads. Modules
stay framework-free (the one-way rule) — the engine call is represented as
structured attributes on the app-side operation span, not a span inside a module.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import logfire

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
    "SPAN_DB_NAME",
    "ObservabilityHandle",
    "configure_observability",
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
    """Wrap one operation execution in a Logfire span (US-SYS-05).

    `operation_id` + `kind` are always set (the drill-down keys on them). The
    input snapshot's *size* is always recorded; its *content* only when the user
    opted into content logging (NFR-SEC-02) — off by default, so no JD/resume
    text ever lands in a span on the default path.
    """
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
