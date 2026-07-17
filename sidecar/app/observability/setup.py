"""Observability configuration (architecture §10, layer 2 — Logfire tracing).

`configure_observability` is the single entry point the app lifespan calls. It
wires Logfire to a **purely-local** span pipeline:

- `send_to_logfire=False` → the SDK never talks to Logfire Cloud. **Zero network
  calls for observability by default** (the A6 hard invariant / NFR-OBS-01).
- A `SimpleSpanProcessor(SqliteSpanExporter)` persists every span to a local
  `logfire.sqlite` synchronously (single-user, low volume — the Logs drill-down
  is immediately consistent).
- An OTLP exporter is added **only** when the user has explicitly enabled export
  *and* given an endpoint (FR-SET-08 / NFR-OBS-02). Off ⇒ no OTLP processor
  exists at all — not a disabled one, absent (the invariant the scope names).

`console=False` is deliberate: the sidecar's stdout carries the `PORT=`/`TOKEN=`
handshake and must never be polluted by span logging.

Reconfiguration (a Settings change to the observability block) re-runs this with
the new flags; Logfire tolerates repeat `configure()` calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import logfire
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from ..logging_setup import get_logger
from .sqlite_exporter import SqliteSpanExporter, init_span_store, prune_spans

SPAN_DB_NAME = "logfire.sqlite"
DEFAULT_RETENTION_DAYS = 30


@dataclass
class ObservabilityHandle:
    """Live observability config the runner + routes read.

    `content_logging` is mutated in place on a Settings change so the runner
    sees the new value without a rebuild; the OTLP fields require a full
    `configure_observability` re-run (they change the processor set)."""

    span_db_path: Path
    content_logging: bool
    otlp_enabled: bool
    otlp_endpoint: str


def _otlp_processor(endpoint: str, headers: dict[str, str] | None) -> Any | None:
    """Build a batched OTLP/HTTP span exporter, or None if it can't be built.

    Import is local so the OTLP proto stack is only touched on the opt-in path;
    a bad endpoint must never crash startup — it degrades to local-only.
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers or None)
        return BatchSpanProcessor(exporter)
    except Exception:  # noqa: BLE001 — export is best-effort; never fail the sidecar
        get_logger().exception("failed to build OTLP exporter for %s", endpoint)
        return None


def configure_observability(
    data_dir: str | Path,
    *,
    content_logging: bool = False,
    otlp_enabled: bool = False,
    otlp_endpoint: str = "",
    otlp_headers: dict[str, str] | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> ObservabilityHandle:
    """Configure Logfire → local SQLite (+ opt-in OTLP). Returns the live handle."""
    span_db_path = Path(data_dir) / SPAN_DB_NAME
    init_span_store(span_db_path)
    prune_spans(span_db_path, older_than_days=retention_days)

    processors: list[Any] = [SimpleSpanProcessor(SqliteSpanExporter(span_db_path))]

    export_on = bool(otlp_enabled and otlp_endpoint.strip())
    if export_on:
        proc = _otlp_processor(otlp_endpoint.strip(), otlp_headers)
        if proc is not None:
            processors.append(proc)
            get_logger().info("OTLP export enabled → %s", otlp_endpoint)
        else:
            export_on = False

    logfire.configure(
        send_to_logfire=False,  # HARD invariant: no network by default (NFR-OBS-01)
        console=False,  # stdout is the handshake channel — never spam it
        additional_span_processors=processors,
    )

    return ObservabilityHandle(
        span_db_path=span_db_path,
        content_logging=content_logging,
        otlp_enabled=export_on,
        otlp_endpoint=otlp_endpoint,
    )


def reconfigure_observability(
    handle: ObservabilityHandle,
    data_dir: str | Path,
    *,
    content_logging: bool,
    otlp_enabled: bool,
    otlp_endpoint: str,
    otlp_headers: dict[str, str] | None,
    retention_days: int,
) -> None:
    """Re-apply observability config after a Settings change, in place.

    The runner holds a reference to `handle`, so we mutate it rather than
    swapping the object — the new `content_logging` flag is then visible to the
    next operation, and Logfire is re-`configure()`d with the new processor set
    (OTLP added/removed). Turning export OFF removes the OTLP processor entirely
    (the hard invariant: off ⇒ no exporter at all).
    """
    fresh = configure_observability(
        data_dir,
        content_logging=content_logging,
        otlp_enabled=otlp_enabled,
        otlp_endpoint=otlp_endpoint,
        otlp_headers=otlp_headers,
        retention_days=retention_days,
    )
    handle.content_logging = fresh.content_logging
    handle.otlp_enabled = fresh.otlp_enabled
    handle.otlp_endpoint = fresh.otlp_endpoint
