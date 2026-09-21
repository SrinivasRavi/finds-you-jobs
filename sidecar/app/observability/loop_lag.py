"""Event-loop lag monitor — sees loop blockage a request timer cannot.

`_RequestObserverMiddleware` (`main.py`) only times requests, and half of what
blocks the loop is not a request at all (a stray sync call off a background
thread, GC, a bad `to_thread` handoff). Worse, head-of-line blocking means the
request that reports slow is usually the victim, not the culprit. This task
sleeps a fixed interval and measures how much later than that it actually
woke: the standard technique for exposing loop blockage from outside any one
request.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from ..logging_setup import get_logger

# Checked every quarter of the shell's 2 s health window: on a periodic
# schedule, any single stall at least this long is guaranteed to straddle a
# wake-up, so it can't slip between checks. Logged past a tenth of the window
# (the same fraction as SLOW_REQUEST_INFO_SECONDS in main.py) — big enough to
# ignore ordinary scheduling jitter, small enough to warn well before a stall
# could cost a /healthz miss.
LOOP_LAG_INTERVAL_SECONDS = 0.5
LOOP_LAG_THRESHOLD_SECONDS = 0.2


async def monitor_loop_lag(
    *,
    interval: float = LOOP_LAG_INTERVAL_SECONDS,
    threshold: float = LOOP_LAG_THRESHOLD_SECONDS,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Loop forever, warning whenever a sleep wakes up later than asked.

    `clock`/`sleep` are injectable so tests use small values instead of real
    waits (mirrors `watch_parent`'s `poll_interval` injection in
    `watchdog.py`). Runs until cancelled by the caller.
    """
    log = get_logger()
    while True:
        started = clock()
        await sleep(interval)
        lag = clock() - started - interval
        if lag >= threshold:
            log.warning(
                "event loop lag: woke %.0f ms late (threshold %.0f ms)",
                lag * 1000,
                threshold * 1000,
            )
