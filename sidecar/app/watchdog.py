"""Parent-pid orphan watchdog (architecture §4.4 step 4).

The sidecar is spawned by the Tauri shell. If the shell dies hard (crash, kill
-9), the OS reparents the sidecar — on POSIX to pid 1 / a subreaper. The
watchdog polls the parent pid and, on reparenting, logs and triggers a clean
shutdown so no zombie children (claude CLI / Chromium / voyager) survive.

The decision is a pure function so it is unit-testable without real reparenting.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable

from .logging_setup import get_logger

POLL_INTERVAL_SECONDS = 2.0


def is_orphaned(original_ppid: int, current_ppid: int) -> bool:
    """True when the sidecar has been reparented away from its original parent.

    Pure decision function. `original_ppid` is captured at startup (the shell's
    pid); `current_ppid` is `os.getppid()` now. A change means the shell died and
    the OS reparented us — or (POSIX) we were reparented to the init/subreaper
    process (pid 1). Either way, we are orphaned and should exit.
    """
    if current_ppid != original_ppid:
        return True
    # Belt-and-suspenders on POSIX: a live shell is never pid 1.
    if current_ppid == 1:
        return True
    return False


async def watch_parent(
    original_ppid: int,
    on_orphaned: Callable[[], Awaitable[None]],
    *,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    get_ppid: Callable[[], int] = os.getppid,
) -> None:
    """Poll the parent pid; call `on_orphaned` once, then stop, when reparented.

    `get_ppid` is injectable for tests. The coroutine ends after firing the
    callback (or when cancelled at shutdown).
    """
    log = get_logger()
    log.debug("orphan watchdog started (original_ppid=%d)", original_ppid)
    while True:
        current = get_ppid()
        if is_orphaned(original_ppid, current):
            log.warning(
                "orphaned: parent pid changed %d -> %d; shutting down",
                original_ppid,
                current,
            )
            await on_orphaned()
            return
        await asyncio.sleep(poll_interval)
