"""Bounded retry for 1 LLM completion plus parse, shared by score, tailor and
cover.

An empty completion or output-contract drift gets a re-ask; a deliberate refusal
(the cover JD gate) never does, and that stays each module's call. Every billed
attempt that produced output is counted (`merge_usage`); a raised attempt
reports no usage (F-L11). Policy (F-H5): deterministic rejections fail fast;
429, 5xx, network and empty content back off with full jitter, honoring
Retry-After; parse drift re-asks at once. Sleeps run on worker threads and poll
for cancellation (F-M7)."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .claude_engine import EngineError

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
    """Seconds to wait before re-asking after attempt `attempt` (1-based), or None
    when it mustn't retry. Reads `EngineError`'s fields via getattr; an explicit
    `retryable` wins over the status table."""
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


class _EngineLike(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Any]: ...


def complete_and_parse[T](
    engine: _EngineLike,
    system_prompt: str,
    user_prompt: str,
    parse: Callable[[str], T],
    parse_error: type[Exception],
    cancelled: Callable[[], bool] | None = None,
) -> tuple[T, dict[str, Any]]:
    """1 completion plus its parse, under the policy above: an engine failure
    backs off or fails fast, a `parse_error` whose `stage` is "parse" is
    re-asked at once, any other stage (a deliberate refusal) raises. Returns
    the parsed value and the merged usage of every attempt that produced
    output, so a retry never hides a billed attempt."""
    billed: list[Any] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raise_if_cancelled(cancelled)
        try:
            raw, usage = engine.complete(system_prompt, user_prompt)
        except EngineError as e:
            delay = retry_delay_s(e, attempt)
            if delay is None:
                raise
            wait_before_retry(delay, cancelled)
            continue
        billed.append(usage)
        try:
            value = parse(raw)
        except parse_error as e:
            stage = getattr(e, "stage", "")
            if stage != "parse" or attempt == MAX_ATTEMPTS:
                raise
            continue
        return value, merge_usage(billed)
    raise AssertionError("unreachable: the loop always returns or raises")
