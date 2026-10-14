# voyager_py/tests/test_voyager_watchdog.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""The voyager fetch watchdog. It used to call `context.close()` from its timer
thread, which sync Playwright refuses, so it never interrupted anything
(architecture-audit-2026-10-14.md section 4.3). It now kills the browser through
the session, the interrupt a hung call honours."""

from __future__ import annotations

import threading

import pytest

from sidecar.packages.referral_outreach.upstream.client import PlaywrightLinkedinAPI
from sidecar.packages.referral_outreach.upstream.errors import BrowserUnresponsiveError


class _Context:
    def cookies(self):
        return [{"name": "JSESSIONID", "value": '"ajax:1"'}]


class _Session:
    def __init__(self) -> None:
        self.page = object()
        self.context = _Context()
        self.killed = threading.Event()

    def kill_browser(self) -> None:
        self.killed.set()


def test_a_hung_fetch_kills_the_browser_and_raises_unresponsive() -> None:
    session = _Session()
    api = PlaywrightLinkedinAPI(session, timeout_ms=100)

    def hung_fetch():
        # Stands in for `page.evaluate` stuck in Chrome: it returns only once the
        # browser under it is killed, and then it fails.
        assert session.killed.wait(5), "the watchdog never killed the browser"
        raise RuntimeError("Target page, context or browser has been closed")

    with pytest.raises(BrowserUnresponsiveError, match="unresponsive"):
        api._run_with_watchdog("GET /voyager/api/me", hung_fetch)
    assert session.killed.is_set()


def test_a_fetch_inside_its_deadline_kills_nothing() -> None:
    session = _Session()
    api = PlaywrightLinkedinAPI(session, timeout_ms=5_000)
    assert api._run_with_watchdog("GET /voyager/api/me", lambda: 42) == 42
    assert not session.killed.is_set()
