"""Observability seam — no-op until the observability commit lands.

The runner wraps every operation in `operation_span(...)` and records its
outcome via `record_span_success` / `record_span_failure` (architecture §10).
This package currently provides that contract as deliberate no-ops so the
runner's span calls are already load-bearing-shaped; the observability commit
replaces the internals with the real local span pipeline without touching the
runner. Spans are additive by contract: a failure inside them must never fail
an operation (the runner enforces this independently).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


@dataclass
class ObservabilityHandle:
    """Live observability config the app threads into the runner.

    `content_logging` mirrors the explicit local debug-content setting: span
    inputs are recorded only when the user turned it on (privacy default off).
    """

    content_logging: bool = False


@contextmanager
def operation_span(
    operation_id: str,
    kind: str,
    *,
    input_snapshot: dict[str, Any] | None = None,
    content_logging: bool = False,
) -> Iterator[None]:
    """Per-operation span. No-op placeholder: yields None as the span object."""
    yield None


def record_span_success(span: Any, outcome: Any) -> None:
    """Record a successful outcome on the span. No-op placeholder."""


def record_span_failure(span: Any, message: str, exc: BaseException) -> None:
    """Record a failure on the span. No-op placeholder."""
