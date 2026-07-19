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


def pid_alive(pid: int) -> bool:
    """Liveness probe for `pid`.

    POSIX: the classic signal-0 no-op check. Windows: NEVER `os.kill(pid, 0)` —
    `signal.CTRL_C_EVENT == 0` there, so that call does not probe anything, it
    SENDS a real Ctrl-C to the whole console. This exact call was the
    2026-07-19 Windows killer: the watchdog's first 2-second "is the shell
    alive?" tick Ctrl-C'd the console group — Tauri exe dead with
    STATUS_CONTROL_C_EXIT, cargo/pnpm wrappers interrupted, on every install.
    Use OpenProcess + GetExitCodeProcess instead (access-denied counts as
    alive: the process exists, we just can't open it)."""
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
    shell_pid: int | None = None,
    is_alive: Callable[[int], bool] = pid_alive,
) -> None:
    """Poll for orphaning; call `on_orphaned` once, then stop.

    Two independent triggers (2026-07-17 dogfood — dev left uv+uvicorn alive):
    - the immediate parent changed (classic reparenting — covers the packaged
      build, where the shell spawns the sidecar binary directly);
    - `shell_pid` (FYJ_SHELL_PID, the Tauri shell's own pid) is no longer
      alive — covers dev, where the immediate parent is the `uv run` wrapper
      that survives the shell and keeps the ppid check blind.

    `get_ppid`/`is_alive` are injectable for tests. The coroutine ends after
    firing the callback (or when cancelled at shutdown).
    """
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
