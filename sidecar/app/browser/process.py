"""Find and kill the Chrome behind one browser profile.

A sync Playwright call that never returns can't be interrupted from another thread
(architecture-audit-2026-10-14.md section 4.3). Killing its Chrome can: the call
then fails, and the surface relaunches. A profile directory is used by exactly 1
Chrome at a time, so `--user-data-dir` names our process and never the user's own."""

from __future__ import annotations

from pathlib import Path

import psutil

from ..logging_setup import get_logger

# How long a killed process tree gets to exit before we stop waiting on it.
KILL_WAIT_SECONDS = 3.0


def _profile_flags(profile_dir: Path) -> set[str]:
    """Both spellings a launch may have passed: as given, and with symlinks resolved
    (macOS temp dirs read `/var/...` and `/private/var/...`)."""
    given = str(profile_dir)
    resolved = str(Path(profile_dir).resolve())
    return {f"--user-data-dir={given}", f"--user-data-dir={resolved}"}


def browser_processes(profile_dir: Path) -> list[psutil.Process]:
    """Every live process launched against `profile_dir`, plus their children."""
    flags = _profile_flags(profile_dir)
    roots: list[psutil.Process] = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(arg in flags for arg in cmdline):
            roots.append(proc)
    found: dict[int, psutil.Process] = {}
    for root in roots:
        found[root.pid] = root
        try:
            for child in root.children(recursive=True):
                found[child.pid] = child
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return list(found.values())


def kill_browser(profile_dir: Path) -> int:
    """Kill every Chrome process on `profile_dir` and wait briefly for them to go.
    Returns how many were signalled; 0 means none was running."""
    procs = browser_processes(profile_dir)
    for proc in procs:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(procs, timeout=KILL_WAIT_SECONDS)
    if alive:
        get_logger().warning(
            "%d Chrome processes on %s outlived the kill", len(alive), profile_dir
        )
    return len(procs)
