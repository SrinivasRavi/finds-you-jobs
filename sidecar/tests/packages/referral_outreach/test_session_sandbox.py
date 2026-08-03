# voyager_py/tests/test_session_sandbox.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Chromium-sandbox launch policy (2026-08-02 headed dogfood: Playwright
launches with the sandbox DISABLED by default — the "--no-sandbox" banner).
Both launch sites must pass chromium_sandbox=True; only a sandbox-signature
launch failure (Linux without unprivileged user namespaces) may retry once
with it off, warned. Stubbed launchers — no real browser."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sidecar.packages.referral_outreach.upstream.session import (
    _launch_browser,
    _launch_persistent,
    _sandboxed_launch,
)

_SANDBOX_FAILURE = (
    "Failed to move to new namespace: PID namespaces supported ... "
    "No usable sandbox! Update your kernel or use --no-sandbox."
)


class StubLaunch:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.calls: list[bool] = []
        self._failures = list(failures or [])

    def __call__(self, sandbox: bool) -> str:
        self.calls.append(sandbox)
        if self._failures:
            raise self._failures.pop(0)
        return "browser"


def test_launches_with_the_sandbox_on() -> None:
    launch = StubLaunch()
    assert _sandboxed_launch(launch) == "browser"
    assert launch.calls == [True]


def test_sandbox_failure_retries_once_without_it() -> None:
    launch = StubLaunch(failures=[RuntimeError(_SANDBOX_FAILURE)])
    assert _sandboxed_launch(launch) == "browser"
    assert launch.calls == [True, False]


def test_non_sandbox_failure_reraises_never_drops_the_sandbox() -> None:
    launch = StubLaunch(failures=[RuntimeError("Chromium distribution 'chrome' is not found")])
    with pytest.raises(RuntimeError, match="not found"):
        _sandboxed_launch(launch)
    assert launch.calls == [True]


class RecordingChromium:
    """Records launch kwargs; optionally fails the channel="chrome" attempt so
    the bundled-Chromium fallback path is exercised."""

    def __init__(self, chrome_missing: bool = False) -> None:
        self.launches: list[dict] = []
        self._chrome_missing = chrome_missing

    def _record(self, kwargs: dict) -> str:
        self.launches.append(kwargs)
        if self._chrome_missing and kwargs.get("channel") == "chrome":
            raise RuntimeError("Chromium distribution 'chrome' is not found")
        return "browser"

    def launch(self, **kwargs) -> str:
        return self._record(kwargs)

    def launch_persistent_context(self, user_data_dir, **kwargs) -> str:
        return self._record({"user_data_dir": user_data_dir, **kwargs})


def test_launch_browser_passes_chromium_sandbox_true() -> None:
    chromium = RecordingChromium()
    pw = SimpleNamespace(chromium=chromium)
    assert _launch_browser(pw, headless=True) == "browser"
    assert chromium.launches[0]["channel"] == "chrome"
    assert chromium.launches[0]["chromium_sandbox"] is True


def test_launch_browser_bundled_fallback_keeps_the_sandbox() -> None:
    chromium = RecordingChromium(chrome_missing=True)
    pw = SimpleNamespace(chromium=chromium)
    assert _launch_browser(pw, headless=True) == "browser"
    # chrome attempt failed (not a sandbox error → no sandbox-off retry), then
    # the bundled build launched — still sandboxed.
    assert [k.get("channel") for k in chromium.launches] == ["chrome", None]
    assert all(k["chromium_sandbox"] is True for k in chromium.launches)


def test_launch_persistent_passes_chromium_sandbox_true(tmp_path) -> None:
    chromium = RecordingChromium()
    pw = SimpleNamespace(chromium=chromium)
    profile_dir = str(tmp_path / "profile")
    assert _launch_persistent(pw, profile_dir, headless=True) == "browser"
    assert chromium.launches[0]["user_data_dir"] == profile_dir
    assert chromium.launches[0]["chromium_sandbox"] is True
