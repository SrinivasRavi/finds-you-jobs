# voyager_py/tests/test_connect_probes.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""The Connect-affordance probe chain (`find_and_click_connect`) against LOCAL
fixture HTML with REAL Chromium — no live LinkedIn (the wire stays cold; the
maintainer dogfoods the live send). Reproduces the four DOM variants behind the
"[voyager] SkipProfile: Top Card section not found" dogfood failure:

  - top-card Connect (Connect-primary layout)      → clicks, returns probe name
  - Connect inside the "More" overflow menu         → opens menu, clicks, returns
    (also exercises the page-wide fallback: this fixture has NO matching top-card
     wrapper — the exact scenario that used to raise "Top Card section not found")
  - invite already Pending                          → typed SkipProfile
  - already connected (Message, no Connect)         → typed SkipProfile
  - no affordance at all                            → SkipProfile naming probes +
                                                       debug-capture path
"""

from __future__ import annotations

from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from types import SimpleNamespace  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

from sidecar.packages.referral_outreach.upstream.actions import find_and_click_connect  # noqa: E402
from sidecar.packages.referral_outreach.upstream.errors import SkipProfile  # noqa: E402
from sidecar.packages.referral_outreach.upstream.session import capture_failure  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - CI without the browser binary
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


def _page_for(browser, fixture: str):
    page = browser.new_page()
    page.goto((_FIXTURES / fixture).as_uri())
    return page


def test_top_card_connect_primary(browser):
    page = _page_for(browser, "connect_top_card.html")
    try:
        assert find_and_click_connect(page, wait_ms=1000) == "top-card-connect"
    finally:
        page.close()


def test_connect_inside_more_menu_with_pagewide_fallback(browser):
    # No matching top-card wrapper here → page-wide fallback; Connect is revealed
    # only after the More menu is opened.
    page = _page_for(browser, "connect_more_menu.html")
    try:
        assert find_and_click_connect(page, wait_ms=2000) == "more-menu-connect"
    finally:
        page.close()


def test_connect_inside_sdui_more_menu(browser):
    # 2026-07-13 live regression: LinkedIn's server-driven-UI profile renders the
    # overflow-menu Connect as an <a href="/preload/custom-invite/…"
    # componentkey="ConnectButton…"> with no role and no aria-label. Every classic
    # probe missed it and the send failed with "no Connect affordance found" even
    # though the menu was open with a visible Connect. The SDUI markers must match
    # and the anchor must actually get clicked.
    page = _page_for(browser, "connect_sdui_more_menu.html")
    try:
        assert find_and_click_connect(page, wait_ms=2000) == "more-menu-connect"
        assert page.evaluate("window.__connectClicked === true")
    finally:
        page.close()


def test_pending_is_typed_skip(browser):
    page = _page_for(browser, "connect_pending.html")
    try:
        with pytest.raises(SkipProfile) as ei:
            find_and_click_connect(page, wait_ms=500)
        assert "Pending" in str(ei.value)
        assert "pending" in str(ei.value)  # probe list names it
    finally:
        page.close()


def test_already_connected_is_typed_skip(browser):
    page = _page_for(browser, "connect_already_connected.html")
    try:
        with pytest.raises(SkipProfile) as ei:
            find_and_click_connect(page, wait_ms=500)
        msg = str(ei.value)
        assert "already connected" in msg
        # Every probe up to the message check must be named.
        for probe in ("pending", "top-card-connect", "more-menu-connect", "message-primary"):
            assert probe in msg
    finally:
        page.close()


def test_no_affordance_names_probes_and_captures(browser):
    page = _page_for(browser, "connect_none.html")
    captured = {}

    def _capture() -> str:
        captured["called"] = True
        return "/tmp/fyj/linkedin/debug/stamp-connect-no-affordance"  # noqa: S108

    try:
        with pytest.raises(SkipProfile) as ei:
            find_and_click_connect(page, wait_ms=300, capture=_capture)
        msg = str(ei.value)
        assert "no Connect affordance found" in msg
        assert "probes tried: [pending, top-card-connect, more-menu-connect, message-primary]" in msg  # noqa: E501
        assert "debug capture: /tmp/fyj/linkedin/debug/" in msg
        assert captured.get("called") is True
    finally:
        page.close()


def test_capture_failure_writes_screenshot_html_and_readme(browser, tmp_path):
    page = _page_for(browser, "connect_none.html")
    storage_state = tmp_path / "linkedin" / "storage_state.json"
    session = SimpleNamespace(page=page, storage_state_path=storage_state)
    try:
        dest = capture_failure(session, "connect-no-affordance")  # type: ignore[arg-type]
    finally:
        page.close()
    dest_path = Path(dest)
    assert dest_path.exists()
    assert (dest_path / "page.png").exists()
    assert (dest_path / "page.html").exists()
    # README warning about local personal data lives once at the debug root.
    readme = storage_state.parent / "debug" / "README.md"
    assert readme.exists()
    assert "personal data" in readme.read_text()



def test_remove_connection_menu_item_is_never_clicked(browser):
    """2026-07-12 live near-miss: on an already-connected profile the More menu
    holds "Remove connection"; a case-insensitive has-text("Connect") probe
    clicked it. The exact-match probe must skip it AND resolve the profile as
    already-connected — and must never have clicked the Remove item."""
    page = _page_for(browser, "connect_connected_remove_menu.html")
    try:
        with pytest.raises(SkipProfile) as ei:
            find_and_click_connect(page, wait_ms=1000)
        assert "already connected" in str(ei.value)
        assert page.evaluate("window.__removeClicked === undefined")
    finally:
        page.close()
