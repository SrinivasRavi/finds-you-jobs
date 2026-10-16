"""Covers: the 1 browser host and its sync proxy (audit-2026-10-14.md section 8.9).

Against REAL Chrome (installed, or Playwright's bundled build): sync calls from a
worker thread drive an async page on the host loop, results come back wrapped
the same way, Playwright's own errors keep their type, and a sync call from the
host loop is refused instead of deadlocking."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sidecar.app.browser.host import BrowserHost, SyncProxy, launch

pytest.importorskip("playwright.async_api")

_PAGE = """
<input id="name"><button id="go" onclick="document.title = 'clicked:' +
  document.getElementById('name').value">Go</button>
<a id="pop" href="about:blank" target="_blank">open</a>
"""


@pytest.fixture(scope="module")
def host() -> Iterator[BrowserHost]:
    h = BrowserHost()
    yield h
    h.shutdown()


@pytest.fixture
def page(host: BrowserHost, tmp_path: Path) -> Iterator[SyncProxy]:
    async def _open() -> object:
        pw = await host.playwright()
        return await launch(pw.chromium, str(tmp_path / "profile"), headless=True)

    try:
        context = host.proxy(host.run(_open, timeout=60))
    except Exception as exc:  # pragma: no cover, CI without any Chromium
        pytest.skip(f"no Chromium to launch: {exc}")
    page = context.pages[0] if context.pages else context.new_page()
    page.set_content(_PAGE)
    yield page
    context.close()


def test_sync_calls_drive_the_page_and_keep_identity(page: SyncProxy) -> None:
    page.locator("#name").fill("Ada")
    page.locator("#go").click()
    assert page.title() == "clicked:Ada"
    assert page.evaluate("() => 6 * 7") == 42
    page.keyboard.press("Tab")
    assert page.context.pages[0] is page  # the same object gets the same proxy


def test_an_event_handler_gets_proxies_and_can_be_removed(page: SyncProxy) -> None:
    popups: list = []

    def on_popup(p) -> None:  # noqa: ANN001
        popups.append(p)

    page.on("popup", on_popup)
    page.locator("#pop").click()
    page.wait_for_timeout(500)
    assert popups and isinstance(popups[0], SyncProxy)
    popups[0].close()
    page.remove_listener("popup", on_popup)
    page.locator("#pop").click()
    page.wait_for_timeout(500)
    assert len(popups) == 1


def test_playwright_errors_keep_their_type(page: SyncProxy) -> None:
    from playwright.sync_api import TimeoutError as SyncTimeout

    with pytest.raises(SyncTimeout):
        page.locator("#missing").click(timeout=200)


def test_a_sync_call_from_the_host_loop_is_refused(host: BrowserHost) -> None:
    with pytest.raises(RuntimeError, match="host's own loop"):
        host.run(lambda: host.run(lambda: 1), timeout=5)


def test_a_handler_on_the_host_loop_can_fire_a_call(page: SyncProxy) -> None:
    """The screencast ack's shape: a handler runs on the host loop, so it cannot
    wait on a call, but it can fire one."""

    def on_console(_msg) -> None:  # noqa: ANN001
        page.fire("evaluate", "() => { document.title = 'fired' }")

    page.on("console", on_console)
    page.evaluate("() => console.log('ping')")
    page.wait_for_timeout(300)
    assert page.title() == "fired"
