"""Covers: finding and killing the Chrome behind 1 profile (`app/browser/process.py`).

Real processes stand in for Chrome: what matters is that the match is on the exact
`--user-data-dir`, so a kill can never reach a browser on another profile."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from sidecar.app.browser.process import browser_processes, kill_browser

_SLEEPER = "import time; time.sleep(60)"


def _spawn(profile: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603, our own interpreter and fixed argv
        [sys.executable, "-c", _SLEEPER, f"--user-data-dir={profile}"]
    )


def _wait_seen(profile: Path, pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pid in {p.pid for p in browser_processes(profile)}:
            return True
        time.sleep(0.05)
    return False


def test_kill_reaches_only_the_profile_it_names(tmp_path: Path) -> None:
    mine, other = tmp_path / "mine", tmp_path / "other"
    target, bystander = _spawn(mine), _spawn(other)
    try:
        assert _wait_seen(mine, target.pid)
        assert target.pid not in {p.pid for p in browser_processes(other)}

        # At least 1: on Windows the venv's python.exe is a launcher that starts
        # the interpreter as its child, so 1 spawn is 2 processes (2026-10-16 CI).
        assert kill_browser(mine) >= 1
        assert target.wait(timeout=5) is not None
        assert bystander.poll() is None  # the other profile's process lives
        assert browser_processes(mine) == []
    finally:
        for proc in (target, bystander):
            proc.kill()
            proc.wait(timeout=5)


def test_kill_with_nothing_running_is_a_no_op(tmp_path: Path) -> None:
    assert kill_browser(tmp_path / "never-launched") == 0
