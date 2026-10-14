"""Parent-pid orphan watchdog (architecture section 4.4 step 4).

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


def pid_alive(pid: int) -> bool:
    """Liveness probe for `pid`. POSIX: signal 0. Windows: never `os.kill(pid, 0)`,
    which sends a real Ctrl-C to the console (it killed every Windows install on
    2026-07-19); OpenProcess plus GetExitCodeProcess instead, access denied
    counting as alive."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True  # exists but unreadable — treat as alive, never kill on doubt
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_orphaned(original_ppid: int, current_ppid: int) -> bool:
    """True when the sidecar was reparented away from its original parent (the
    shell), including to pid 1 on POSIX. Pure."""
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
    shell_pid: int | None = None,
    is_alive: Callable[[int], bool] = pid_alive,
) -> None:
    """Poll for orphaning; call `on_orphaned` once, then stop. 2 triggers: the
    immediate parent changed (the packaged build), or `shell_pid` (FYJ_SHELL_PID)
    died (dev, where `uv run` outlives the shell). `get_ppid` and `is_alive` are
    injectable."""
    log = get_logger()
    log.debug(
        "orphan watchdog started (original_ppid=%d shell_pid=%s)",
        original_ppid,
        shell_pid,
    )
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
        if shell_pid is not None and not is_alive(shell_pid):
            log.warning(
                "orphaned: shell pid %d is gone (wrapper parent %d still alive); "
                "shutting down",
                shell_pid,
                current,
            )
            await on_orphaned()
            return
        await asyncio.sleep(poll_interval)
