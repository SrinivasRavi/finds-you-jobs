"""Local span store — a SQLite `SpanExporter` (architecture §10, layer 2).

The Logfire SDK is built on OpenTelemetry, so persisting spans locally is a
matter of attaching a custom OTel `SpanExporter`. This one writes each finished
span as a row in a standalone `logfire.sqlite` file — **not** the app's Alembic
schema. That satisfies US-SYS-05's "No separate `Trace` table in the schema; the
observability SDK manages its own log": the span log lives in its own file, on
its own (non-migrated) shape, owned by the observability pipeline.

Why a hand-rolled SQLite exporter (the local-store choice):

- Logfire's durable path is its **cloud** platform (OTLP over the network). Its
  offline story is `send_to_logfire=False` + whatever span processors you add —
  there is no built-in queryable local SQLite sink. NFR-OBS-01 demands exactly
  that: a local, queryable `logfire.sqlite`, 30-day retention, nothing leaving
  the machine. A ~100-line exporter is the simplest durable store that (a) the
  Logs drill-down can query by `operation_id`, (b) survives restart, and (c)
  makes zero network calls. The OTLP file exporters write protobuf/JSON blobs,
  not something the UI can query — rejected.

Thread-safety: OTel calls `export()` from a processor thread. We open a fresh
short-lived connection per call (WAL, autocommit) rather than sharing one — the
volume is tiny (single-user desktop), so simplicity wins over pooling.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

# Attribute keys the runner sets on every operation span (US-SYS-05 list). Pulled
# into their own indexed/queryable columns; the rest live in the attributes JSON.
_OP_ID_ATTR = "operation_id"
_KIND_ATTR = "kind"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id        TEXT PRIMARY KEY,
    trace_id       TEXT NOT NULL,
    parent_span_id TEXT,
    name           TEXT NOT NULL,
    operation_id   TEXT,
    op_kind        TEXT,
    start_ns       INTEGER NOT NULL,
    end_ns         INTEGER NOT NULL,
    duration_ms    REAL NOT NULL,
    status         TEXT NOT NULL,
    attributes     TEXT NOT NULL,
    events         TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_spans_operation_id ON spans (operation_id);
CREATE INDEX IF NOT EXISTS ix_spans_start ON spans (start_ns);
"""


def init_span_store(path: str | Path) -> None:
    """Create the `logfire.sqlite` schema if absent (idempotent)."""
    conn = _connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _jsonable(value: Any) -> Any:
    """OTel attribute values are already scalars/sequences; guard the rest."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _serialize_events(span: ReadableSpan) -> str:
    events = [
        {
            "name": ev.name,
            "timestamp_ns": ev.timestamp,
            "attributes": {k: _jsonable(v) for k, v in dict(ev.attributes or {}).items()},
        }
        for ev in span.events
    ]
    return json.dumps(events, separators=(",", ":"))


class SqliteSpanExporter(SpanExporter):
    """Persist finished spans to a local `logfire.sqlite` (no network)."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        init_span_store(self._path)

    def export(self, spans: Any) -> SpanExportResult:
        rows: list[tuple[Any, ...]] = []
        for span in spans:
            ctx = span.get_span_context()
            attrs = {k: _jsonable(v) for k, v in dict(span.attributes or {}).items()}
            start_ns = span.start_time or 0
            end_ns = span.end_time or start_ns
            rows.append(
                (
                    format(ctx.span_id, "016x"),
                    format(ctx.trace_id, "032x"),
                    format(span.parent.span_id, "016x") if span.parent else None,
                    span.name,
                    attrs.get(_OP_ID_ATTR),
                    attrs.get(_KIND_ATTR),
                    start_ns,
                    end_ns,
                    (end_ns - start_ns) / 1_000_000,
                    span.status.status_code.name,
                    json.dumps(attrs, separators=(",", ":")),
                    _serialize_events(span),
                    datetime.now(UTC).isoformat(),
                )
            )
        if not rows:
            return SpanExportResult.SUCCESS
        try:
            conn = _connect(self._path)
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO spans (span_id, trace_id, parent_span_id, "
                    "name, operation_id, op_kind, start_ns, end_ns, duration_ms, status, "
                    "attributes, events, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: ARG002
        return True

    def shutdown(self) -> None:  # OTel lifecycle hook — connections are per-call.
        return None


# ---------------------------------------------------------------------------
# Read side (the Logs drill-down + retention) — plain queries over the file.
# ---------------------------------------------------------------------------


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "span_id": row["span_id"],
        "trace_id": row["trace_id"],
        "parent_span_id": row["parent_span_id"],
        "name": row["name"],
        "operation_id": row["operation_id"],
        "op_kind": row["op_kind"],
        "start_ns": row["start_ns"],
        "end_ns": row["end_ns"],
        "duration_ms": row["duration_ms"],
        "status": row["status"],
        "attributes": json.loads(row["attributes"]),
        "events": json.loads(row["events"]),
        "created_at": row["created_at"],
    }


def read_spans_for_operation(path: str | Path, operation_id: str) -> list[dict[str, Any]]:
    """Every span carrying this `operation_id`, oldest-first (the drill-down)."""
    if not Path(path).exists():
        return []
    conn = _connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM spans WHERE operation_id = ? ORDER BY start_ns ASC",
            (operation_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def span_count(path: str | Path) -> int:
    if not Path(path).exists():
        return 0
    conn = _connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0])
    finally:
        conn.close()


def prune_spans(path: str | Path, *, older_than_days: int, now_ns: int | None = None) -> int:
    """Drop spans older than the retention window (NFR-OBS-01). Returns #deleted."""
    if not Path(path).exists() or older_than_days <= 0:
        return 0
    import time

    cutoff_ns = (now_ns if now_ns is not None else time.time_ns()) - (
        older_than_days * 86_400 * 1_000_000_000
    )
    conn = _connect(path)
    try:
        cur = conn.execute("DELETE FROM spans WHERE start_ns < ?", (cutoff_ns,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
