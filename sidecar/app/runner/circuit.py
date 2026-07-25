"""Per-engine circuit breaker (technical audit F-H5).

A dead or auth-broken provider must not grind a 50-job score fan-out through
every queued operation × its full retry/timeout budget. The runner records
each LLM operation's outcome per routed engine NAME (the registry key — e.g.
"openrouter", "claude-cli"); after `FAILURE_THRESHOLD` consecutive engine
failures the circuit for that name opens, and further operations routed to it
fail *fast* with a distinct, honest ledger message instead of holding an llm
slot. After `COOLDOWN_S` one probe operation is allowed through (half-open):
success closes the circuit, failure re-opens it for another cooldown.

Only `EngineError` outcomes feed the breaker — module parse drift, missing
rows, or bugs in our own code say nothing about the provider's health. Pure
decision logic (injectable clock), unit-testable without threads or the DB,
mirroring `policy.can_start`'s style.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

# Consecutive engine failures before the circuit opens. Each failed operation
# already represents up to MAX_ATTEMPTS classified engine calls, so this is a
# deliberately confident "the provider is down" signal, not a blip.
FAILURE_THRESHOLD = 5
# How long an open circuit rejects operations before allowing one probe.
COOLDOWN_S = 60.0


class ProviderCircuitOpen(RuntimeError):
    """Fail-fast outcome: the routed provider's circuit is open. NOT an
    EngineError on purpose — a rejection must never feed the breaker that
    produced it."""


@dataclass
class _Circuit:
    consecutive: int = 0
    opened_at: float | None = None  # monotonic time the circuit opened
    probing: bool = False  # a half-open probe is in flight
    last_error: str = ""


@dataclass
class EngineCircuitBreaker:
    threshold: int = FAILURE_THRESHOLD
    cooldown_s: float = COOLDOWN_S
    monotonic: Callable[[], float] = time.monotonic
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _circuits: dict[str, _Circuit] = field(default_factory=dict, repr=False)

    def check(self, name: str) -> bool:
        """Gate one operation routed to engine `name`. Returns when the call
        may proceed — False for a closed circuit, True when this caller holds
        the half-open probe LEASE (the caller must then settle it: success /
        engine-failure records it, any other terminal outcome must call
        `abandon_probe`); raises `ProviderCircuitOpen` with a clear
        user-facing message when the provider is presumed down."""
        with self._lock:
            circuit = self._circuits.get(name)
            if circuit is None or circuit.opened_at is None:
                return False
            elapsed = self.monotonic() - circuit.opened_at
            if elapsed >= self.cooldown_s and not circuit.probing:
                # Half-open: exactly one probe goes through; its outcome
                # (record_success / record_failure / abandon_probe) decides
                # the circuit.
                circuit.probing = True
                return True
            remaining = max(self.cooldown_s - elapsed, 1.0)
            detail = f" (last error: {circuit.last_error})" if circuit.last_error else ""
            raise ProviderCircuitOpen(
                f"provider {name!r} paused after "
                f"{circuit.consecutive} consecutive failures{detail}; "
                f"new attempts accepted in ~{math.ceil(remaining)}s"
            )

    def record_success(self, name: str) -> None:
        """An engine-backed operation succeeded: the provider is healthy."""
        with self._lock:
            self._circuits.pop(name, None)

    def record_failure(self, name: str, message: str) -> None:
        """An engine-backed operation failed with an `EngineError`."""
        with self._lock:
            circuit = self._circuits.setdefault(name, _Circuit())
            circuit.consecutive += 1
            circuit.last_error = message[:300]
            if circuit.consecutive >= self.threshold:
                # Opens the circuit — or, for a failed half-open probe /
                # stragglers already in flight when it opened, pushes the
                # cooldown out from *now* (still failing = still down).
                circuit.opened_at = self.monotonic()
                circuit.probing = False

    def abandon_probe(self, name: str) -> None:
        """The half-open probe ended with NO verdict on the provider's health
        (cancelled by the user, or failed with a non-`EngineError` — parse
        drift, our own bugs). Release the probe lease so the next `check`
        after cooldown admits a NEW probe; `opened_at` is untouched, so the
        circuit stays open. Without this, a cancelled/derailed probe would
        wedge the circuit open forever (probing never cleared — 2026-07-25)."""
        with self._lock:
            circuit = self._circuits.get(name)
            if circuit is not None:
                circuit.probing = False
