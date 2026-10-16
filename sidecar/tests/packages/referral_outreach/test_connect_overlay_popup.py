# voyager_py/tests/test_connect_overlay_popup.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Decision logic for the two 2026-08-02 live-send hardenings, with fakes (no
browser — the logic under test is pure control flow):

  - `_click_through_overlay`: a trial click reads the hit target. If an SDUI
    overlay intercepts it, the click event goes to the target ONCE (a force
    click still lands on the overlay, 2026-10-16); every other failure
    re-raises unchanged.
  - `_adopt_or_close_popups`: a popup spawned by the SDUI custom-invite anchor
    is adopted iff it hosts the invite compose; blank/irrelevant popups are
    closed; no popup → the main page, with no bounded settle wait paid.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402

from sidecar.packages.referral_outreach.upstream.actions import (  # noqa: E402
    _adopt_or_close_popups,
    _click_through_overlay,
)

_INTERCEPT_MSG = (
    'Locator.click: Timeout 10000ms exceeded.\n'
    '<div id="interop-outlet" data-testid="interop-shadowdom"></div> '
    'intercepts pointer events'
)


class FakeLocator:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.calls: list[dict] = []
        self.dispatched: list[str] = []
        self._failures = list(failures or [])

    def click(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self._failures:
            raise self._failures.pop(0)

    def dispatch_event(self, event: str) -> None:
        self.dispatched.append(event)


def test_clean_click_never_forces_or_dispatches() -> None:
    loc = FakeLocator()
    _click_through_overlay(loc)
    assert loc.calls[0]["trial"] is True
    assert all("force" not in c for c in loc.calls)
    assert loc.dispatched == []


def test_overlay_intercept_dispatches_the_click_to_the_target_once() -> None:
    loc = FakeLocator(failures=[PlaywrightTimeoutError(_INTERCEPT_MSG)])
    _click_through_overlay(loc)
    assert len(loc.calls) == 1  # the trial only: no press lands on the overlay
    assert loc.dispatched == ["click"]


def test_unrelated_timeout_reraises_without_dispatch() -> None:
    loc = FakeLocator(failures=[PlaywrightTimeoutError("Timeout 10000ms exceeded: detached")])
    with pytest.raises(PlaywrightTimeoutError):
        _click_through_overlay(loc)
    assert len(loc.calls) == 1
    assert loc.dispatched == []


def test_non_timeout_error_propagates_unchanged() -> None:
    loc = FakeLocator(failures=[PlaywrightError("browser gone")])
    with pytest.raises(PlaywrightError):
        _click_through_overlay(loc)
    assert len(loc.calls) == 1
    assert loc.dispatched == []


class FakePopup:
    def __init__(self, url: str = "about:blank", compose: bool = False) -> None:
        self.url = url
        self.closed = False
        self._compose = compose

    def wait_for_load_state(self, *args, **kwargs) -> None:
        pass

    def is_closed(self) -> bool:
        return self.closed

    def locator(self, _sel: str):
        return SimpleNamespace(count=lambda: 1 if self._compose else 0)

    def close(self) -> None:
        self.closed = True


def _main_page():
    pumped: list[int] = []
    page = SimpleNamespace(wait_for_timeout=pumped.append)
    return page, pumped


def test_no_popup_returns_main_page() -> None:
    page, pumped = _main_page()
    assert _adopt_or_close_popups(page, []) is page
    # Only the short creation-race pump runs — never the bounded settle wait.
    assert pumped == [300]


def test_blank_popup_is_closed_main_page_kept() -> None:
    page, _ = _main_page()
    blank = FakePopup(url="about:blank")
    assert _adopt_or_close_popups(page, [blank]) is page
    assert blank.closed


def test_compose_popup_is_adopted_not_closed() -> None:
    page, _ = _main_page()
    compose = FakePopup(url="https://www.linkedin.com/preload/custom-invite/x", compose=True)
    assert _adopt_or_close_popups(page, [compose]) is compose
    assert not compose.closed


def test_compose_popup_adopted_siblings_closed() -> None:
    page, _ = _main_page()
    blank = FakePopup(url="about:blank")
    compose = FakePopup(url="https://www.linkedin.com/preload/custom-invite/x", compose=True)
    extra = FakePopup(url="https://www.linkedin.com/preload/custom-invite/y", compose=True)
    assert _adopt_or_close_popups(page, [blank, compose, extra]) is compose
    assert blank.closed and extra.closed and not compose.closed


def test_irrelevant_navigated_popup_is_closed() -> None:
    page, _ = _main_page()
    other = FakePopup(url="https://www.linkedin.com/feed/", compose=False)
    assert _adopt_or_close_popups(page, [other]) is page
    assert other.closed
