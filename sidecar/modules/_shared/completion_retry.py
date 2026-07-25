"""Bounded retry support for one LLM completion + parse step, shared by the
score/tailor/cover black boxes (extracted at the third consumer, per the M1
playbook — see `skill_md.py`'s own extraction note).

A single non-deterministic completion occasionally comes back empty (a
provider hiccup, or a reasoning model spending its whole token budget on
hidden reasoning) or drifts off the module's strict `===...===` output
contract. Both are worth one immediate re-ask before the whole operation
fails and the user has to notice a FAILED row and click Retry by hand. A
deliberate business-logic outcome (the cover module's JD-gate `REFUSED:`) is
NOT a transient failure and must never be retried — that decision stays in
each module's own retry predicate, not here.

Retries mean more than one billed completion can precede a success; `usd`
being an honest ledger (never swallowed, never guessed) means every billed
attempt's cost/tokens must be counted, not just the winning one — that's what
`merge_usage` is for. A completion that raises before returning has no usage
to bill (the `Engine.complete()` contract only returns usage on success —
this includes a timed-out attempt, whose spend the provider may still bill
but never reports back to us; acknowledged, unattributable — F-L11), so only
attempts that *produced output* (whether or not it parsed) contribute.

Retry POLICY (technical audit F-H5): engine failures are classified via the
structured fields `EngineError` carries (`status` / `retryable` /
`retry_after_s` — see `claude_engine.EngineError`). Deterministic provider
rejections (bad key, bad request, missing model) fail fast — re-asking can
never fix them and only burns slot time. Transient failures (429, 5xx,
network, empty content) retry after an exponential backoff with full jitter,
honoring the provider's `Retry-After` when sent, so a rate-limited fan-out
stops hammering in lockstep. Parse-contract drift keeps its immediate re-ask
(it's model non-determinism, not provider load). The backoff sleeps run on
the runner's worker threads (module code never runs on the event loop), so
`time.sleep` is safe here. `wait_before_retry` also polls a cancellation
check so a user's Stop lands between attempts, not after them (F-M7).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

# 1 initial attempt + 2 retries. LLM output is non-deterministic enough that a
# same-prompt re-ask clears most transient empty-content/parse-contract misses;
# past that it's very likely a persistent problem worth surfacing, not masking.
MAX_ATTEMPTS = 3

# Exponential backoff with FULL jitter: delay = uniform(0, min(cap, base·2^k)).
# Jitter decorrelates a 50-job fan-out that all hit the same 429 together.
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0
# A provider's Retry-After is honored verbatim, but capped — an operation the
# user is watching must not silently sleep for minutes on one header.
RETRY_AFTER_CAP_S = 60.0

# Deterministic HTTP rejections a same-input retry can never fix: bad request /
# auth / payment / permission / unknown model / method / payload / validation.
_FAIL_FAST_STATUSES = frozenset({400, 401, 402, 403, 404, 405, 413, 422})

# How often a backoff wait wakes to poll the cancellation check.
_CANCEL_POLL_S = 0.2


class CompletionCancelled(Exception):
    """The user cancelled the operation; raised from a cancellation checkpoint
    (loop top or mid-backoff). The runner maps this onto the `cancelled`
    operation state — it is an outcome, never an error to retry."""


def raise_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise CompletionCancelled("operation cancelled by the user")


def retry_delay_s(
    exc: BaseException,
    attempt: int,
    *,
    rng: Callable[[float, float], float] = random.uniform,
) -> float | None:
    """How long to wait before re-asking after failed attempt number `attempt`
    (1-based), or None when the failure must NOT be retried (deterministic
    rejection, an explicit `retryable=False`, or attempts exhausted).

    Reads `EngineError`'s structured fields via getattr so a module-typed
    error that carries the same fields classifies identically. An explicit
    `retryable` from the raise site always wins over the status table."""
    if attempt >= MAX_ATTEMPTS:
        return None
    retryable = getattr(exc, "retryable", None)
    if retryable is False:
        return None
    status = getattr(exc, "status", None)
    if retryable is not True and status in _FAIL_FAST_STATUSES:
        return None
    retry_after = getattr(exc, "retry_after_s", None)
    if retry_after is not None:
        return min(max(float(retry_after), 0.0), RETRY_AFTER_CAP_S)
    return rng(0.0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (attempt - 1))))


def wait_before_retry(
    delay_s: float,
    cancelled: Callable[[], bool] | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Sleep `delay_s` in short slices, polling `cancelled` between slices so a
    user's Stop interrupts the backoff (raising `CompletionCancelled`) instead
    of waiting it out. Worker-thread only — never call on the event loop."""
    deadline = monotonic() + delay_s
    while True:
        raise_if_cancelled(cancelled)
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        sleep(min(_CANCEL_POLL_S, remaining))


class _UsageLike(Protocol):
    internal_calls: int
    tokens_in: int | None
    tokens_out: int | None
    usd: float | None
    latency_ms: int | None
    model: str | None


def merge_usage(usages: Sequence[_UsageLike]) -> dict[str, Any]:
    """Sum a list of billed attempts into one usage record (as a dict — the
    caller constructs its own module-typed `Usage` from it). A field stays
    `None` only when every attempt reported `None` for it (an honest "still
    unknown", never a fabricated 0); otherwise missing values count as 0 so
    one attempt's known cost is never blanked out by a sibling's gap."""

    def _sum(attr: str) -> float | None:
        values = [getattr(u, attr) for u in usages]
        if all(v is None for v in values):
            return None
        return sum(v or 0 for v in values)

    return {
        "internal_calls": sum(u.internal_calls for u in usages),
        "tokens_in": _sum("tokens_in"),
        "tokens_out": _sum("tokens_out"),
        "usd": _sum("usd"),
        "latency_ms": _sum("latency_ms"),
        # The winning attempt's model — retries always target the same routed
        # engine/model, so this is never actually a mix in practice.
        "model": usages[-1].model,
    }
