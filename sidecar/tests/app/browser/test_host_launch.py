"""Covers: the 1 launch policy every Chrome goes through (`host.launch`).

Installed Chrome first, the bundled build second, the sandbox always on except 1
warned retry after a sandbox failure (a Linux host without user namespaces).
Stubbed launchers, no real browser."""

from __future__ import annotations

import pytest

from sidecar.app.browser import host

_SANDBOX_FAILURE = (
    "Failed to move to new namespace: PID namespaces supported ... "
    "No usable sandbox! Update your kernel or use --no-sandbox."
)
_NO_CHROME = "Chromium distribution 'chrome' is not found"


class RecordingChromium:
    """Records each launch's kwargs and raises the queued failures in order."""

    def __init__(self, *failures: str) -> None:
        self.launches: list[dict] = []
        self._failures = list(failures)

    def _record(self, kwargs: dict) -> str:
        self.launches.append(kwargs)
        if self._failures:
            raise RuntimeError(self._failures.pop(0))
        return "browser"

    async def launch(self, **kwargs) -> str:
        return self._record(kwargs)

    async def launch_persistent_context(self, user_data_dir, **kwargs) -> str:
        return self._record({"user_data_dir": user_data_dir, **kwargs})


@pytest.fixture(autouse=True)
def _prefer_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "PREFERRED_CHANNEL", "chrome")


async def test_a_browser_launches_on_installed_chrome_with_the_sandbox_on() -> None:
    chromium = RecordingChromium()
    assert await host.launch(chromium, headless=True) == "browser"
    assert chromium.launches == [{"channel": "chrome", "chromium_sandbox": True, "headless": True}]


async def test_a_profile_launches_a_persistent_context(tmp_path) -> None:
    chromium = RecordingChromium()
    profile = str(tmp_path / "profile")
    assert await host.launch(chromium, profile, headless=True) == "browser"
    assert chromium.launches[0]["user_data_dir"] == profile
    assert chromium.launches[0]["chromium_sandbox"] is True


async def test_a_sandbox_failure_retries_once_without_it() -> None:
    chromium = RecordingChromium(_SANDBOX_FAILURE)
    assert await host.launch(chromium) == "browser"
    assert [k["chromium_sandbox"] for k in chromium.launches] == [True, False]


async def test_no_chrome_falls_back_to_the_bundled_build_and_keeps_the_sandbox() -> None:
    chromium = RecordingChromium(_NO_CHROME)
    assert await host.launch(chromium) == "browser"
    assert [k.get("channel") for k in chromium.launches] == ["chrome", None]
    assert all(k["chromium_sandbox"] is True for k in chromium.launches)


async def test_any_other_failure_on_the_bundled_build_reraises() -> None:
    chromium = RecordingChromium(_NO_CHROME, "profile is locked")
    with pytest.raises(RuntimeError, match="locked"):
        await host.launch(chromium)
    assert all(k["chromium_sandbox"] is True for k in chromium.launches)
