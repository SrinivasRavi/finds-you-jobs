"""Covers: the shared completion retry POLICY (`_shared/completion_retry.py`,
technical audit F-H5/F-M7).

Classification (fail-fast vs retry) from `EngineError`'s structured fields,
exponential backoff bounds with full jitter, `Retry-After` honored (and
capped), and the cancellable backoff wait. Pure unit tests — injected rng /
sleep / clock, no threads, no network.
"""

from __future__ import annotations

import pytest

from sidecar.modules._shared.claude_engine import EngineError
from sidecar.modules._shared.completion_retry import (
    BACKOFF_BASE_S,
    BACKOFF_CAP_S,
    MAX_ATTEMPTS,
    RETRY_AFTER_CAP_S,
    CompletionCancelled,
    raise_if_cancelled,
    retry_delay_s,
    wait_before_retry,
)

# rng that always returns the upper bound — makes the schedule deterministic
# and lets the tests pin the exponential envelope exactly.
_MAX_RNG = lambda lo, hi: hi  # noqa: E731
_MIN_RNG = lambda lo, hi: lo  # noqa: E731


# ---------------------------------------------------------------------------
# Classification: status → fail-fast vs retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 405, 413, 422])
def test_deterministic_http_rejections_fail_fast(status: int) -> None:
    exc = EngineError(f"API {status}: nope", status=status)
    assert retry_delay_s(exc, 1) is None  # not even a first retry


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 529])
def test_transient_http_failures_retry(status: int) -> None:
    exc = EngineError(f"API {status}: overloaded", status=status)
    assert retry_delay_s(exc, 1, rng=_MAX_RNG) is not None


def test_unclassified_engine_error_stays_retryable() -> None:
    # No status, no override — the pre-audit behavior (empty content, network
    # hiccup) keeps its bounded re-ask.
    assert retry_delay_s(EngineError("empty content"), 1, rng=_MAX_RNG) is not None


def test_explicit_retryable_false_wins_over_everything() -> None:
    # CLI timeout / missing binary: the raise site knows best.
    exc = EngineError("codex CLI timed out after 600s", retryable=False)
    assert retry_delay_s(exc, 1) is None
    exc = EngineError("weird 503", status=503, retryable=False)
    assert retry_delay_s(exc, 1) is None


def test_explicit_retryable_true_overrides_the_status_table() -> None:
    exc = EngineError("a 404 the raise site knows is transient", status=404, retryable=True)
    assert retry_delay_s(exc, 1, rng=_MAX_RNG) is not None


def test_attempts_exhausted_never_retries() -> None:
    exc = EngineError("overloaded", status=503)
    assert retry_delay_s(exc, MAX_ATTEMPTS) is None
    assert retry_delay_s(exc, MAX_ATTEMPTS + 1) is None


# ---------------------------------------------------------------------------
# Backoff schedule: exponential envelope, full jitter, Retry-After
# ---------------------------------------------------------------------------


def test_backoff_envelope_doubles_per_attempt() -> None:
    exc = EngineError("overloaded", status=503)
    # Full jitter: uniform(0, base·2^(attempt-1)) — _MAX_RNG pins the ceiling.
    assert retry_delay_s(exc, 1, rng=_MAX_RNG) == pytest.approx(BACKOFF_BASE_S)
    assert retry_delay_s(exc, 2, rng=_MAX_RNG) == pytest.approx(BACKOFF_BASE_S * 2)
    # And the floor really is 0 (full jitter, not equal jitter).
    assert retry_delay_s(exc, 1, rng=_MIN_RNG) == 0.0


def test_backoff_envelope_is_capped() -> None:
    exc = EngineError("overloaded", status=503)
    seen_hi: list[float] = []

    def spy_rng(lo: float, hi: float) -> float:
        seen_hi.append(hi)
        return hi

    for attempt in range(1, MAX_ATTEMPTS):
        retry_delay_s(exc, attempt, rng=spy_rng)
    assert all(hi <= BACKOFF_CAP_S for hi in seen_hi)


def test_jitter_stays_within_bounds() -> None:
    exc = EngineError("overloaded", status=500)
    for _ in range(50):
        delay = retry_delay_s(exc, 2)
        assert delay is not None
        assert 0.0 <= delay <= BACKOFF_BASE_S * 2


def test_retry_after_is_honored_verbatim_over_computed_backoff() -> None:
    exc = EngineError("rate limited", status=429, retry_after_s=7.0)
    assert retry_delay_s(exc, 1, rng=_MAX_RNG) == 7.0
    assert retry_delay_s(exc, 2, rng=_MAX_RNG) == 7.0


def test_retry_after_is_capped_and_floored() -> None:
    long_wait = EngineError("rate limited", status=429, retry_after_s=999.0)
    assert retry_delay_s(long_wait, 1) == RETRY_AFTER_CAP_S
    negative = EngineError("clock skew", status=429, retry_after_s=-5.0)
    assert retry_delay_s(negative, 1) == 0.0


# ---------------------------------------------------------------------------
# Cancellable backoff wait (F-M7)
# ---------------------------------------------------------------------------


def test_wait_before_retry_sleeps_the_full_delay_in_slices() -> None:
    slept: list[float] = []
    clock = {"now": 0.0}

    def fake_sleep(s: float) -> None:
        slept.append(s)
        clock["now"] += s

    wait_before_retry(1.0, None, sleep=fake_sleep, monotonic=lambda: clock["now"])
    assert sum(slept) == pytest.approx(1.0)
    assert all(s <= 0.2 + 1e-9 for s in slept)  # short slices → responsive Stop


def test_wait_before_retry_raises_mid_wait_when_cancelled() -> None:
    slept: list[float] = []
    clock = {"now": 0.0}

    def fake_sleep(s: float) -> None:
        slept.append(s)
        clock["now"] += s

    # Cancel lands after the first slice — the wait must abort, not run out.
    def cancelled() -> bool:
        return len(slept) >= 1

    with pytest.raises(CompletionCancelled):
        wait_before_retry(30.0, cancelled, sleep=fake_sleep, monotonic=lambda: clock["now"])
    assert sum(slept) < 1.0  # nowhere near the 30s delay


def test_wait_before_retry_zero_delay_returns_immediately() -> None:
    def no_sleep(_s: float) -> None:
        raise AssertionError("must not sleep for a zero delay")

    wait_before_retry(0.0, None, sleep=no_sleep)


def test_raise_if_cancelled() -> None:
    raise_if_cancelled(None)  # no token → never cancelled
    raise_if_cancelled(lambda: False)
    with pytest.raises(CompletionCancelled):
        raise_if_cancelled(lambda: True)
