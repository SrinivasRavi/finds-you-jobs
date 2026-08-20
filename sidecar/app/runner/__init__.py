"""The Operation Runner (architecture section 5.3) — the app's core primitive.

A durable `operations` queue + a thread pool + a per-kind concurrency policy.
Every state change persists and publishes a typed SSE event; every completion
writes the module's `Usage` into the ledger; boot recovery re-enqueues `queued`
and fails orphaned `running` rows (NFR-LONG-02).
"""

from __future__ import annotations

from .circuit import EngineCircuitBreaker, ProviderCircuitOpen
from .policy import DEFAULT_POLICY, ConcurrencyPolicy, can_start
from .runner import RESTART_NOTE, OperationRunner

__all__ = [
    "DEFAULT_POLICY",
    "RESTART_NOTE",
    "ConcurrencyPolicy",
    "EngineCircuitBreaker",
    "OperationRunner",
    "ProviderCircuitOpen",
    "can_start",
]
