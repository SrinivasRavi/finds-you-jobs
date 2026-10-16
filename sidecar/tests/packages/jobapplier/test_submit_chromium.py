# finds-you-jobs — AGPL-3.0-only.
"""The app-side submit executor against real headless Chromium and local
file:// fixtures — no model, ZERO network. Covers the deny-list that keeps
"Apply Later" from outranking the real control, the confirmation read-back,
the validation-error path, and the honest outcome when no control exists.

The agent loop still cannot submit: ``actions.py`` has no submit tool. This
executor is reached only by the app, once, on the user's own Submit click.
"""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from sidecar.packages.jobapplier import submit as submit_mod
from sidecar.packages.jobapplier.submit import submit_application

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_url(name: str) -> str:
    return (FIXTURES / name).as_uri()


async def _submit(url: str, *, before=None):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(url)
            if before is not None:
                await before(page)
            return await submit_application(page)
        finally:
            await browser.close()


async def test_clicks_the_real_control_and_reads_back_the_confirmation() -> None:
    outcome = await _submit(_fixture_url("submit_form.html"))

    assert outcome.clicked
    assert outcome.confirmed
    assert "Submit application" in outcome.note
    # The decoys never won: neither the "Apply Later" button nor "Save job".
    assert "Later" not in outcome.note
    assert "Save" not in outcome.note


async def test_validation_error_is_reported_as_not_confirmed() -> None:
    async def clear_required(page):
        await page.fill("[name=why]", "")

    outcome = await _submit(_fixture_url("submit_form.html"), before=clear_required)

    assert outcome.clicked
    assert not outcome.confirmed
    assert "validation errors" in outcome.note


async def test_a_page_with_only_decoys_finds_nothing(tmp_path: Path) -> None:
    page_file = tmp_path / "decoys.html"
    page_file.write_text(
        "<html><body><button type='button'>Apply Later</button>"
        "<a href='#'>Save job</a><button type='button'>Cancel</button>"
        "</body></html>",
        encoding="utf-8",
    )

    outcome = await _submit(page_file.as_uri())

    assert not outcome.clicked
    assert not outcome.confirmed
    assert outcome.note == "no submit control found"


async def test_no_confirmation_within_the_window_is_stated_not_assumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A submit that neither confirms nor errors must never be reported as
    # confirmed. The wait is shortened so the test stays fast.
    monkeypatch.setattr(submit_mod, "_CONFIRM_POLL_S", 0.05)
    monkeypatch.setattr(submit_mod, "_CONFIRM_WAIT_S", 0.15)
    page_file = tmp_path / "silent.html"
    page_file.write_text(
        "<html><body><form onsubmit='return false'>"
        "<input name='a' value='x'>"
        "<button type='submit'>Submit application</button>"
        "</form></body></html>",
        encoding="utf-8",
    )

    outcome = await _submit(page_file.as_uri())

    assert outcome.clicked
    assert not outcome.confirmed
    assert outcome.unconfirmed  # past the click, silence is doubt, never "not sent"
    assert "no confirmation within" in outcome.note


# --- AUD-4, the Applier half: a Submit that ends in doubt is settled by reading ---

_COUNTING_FORM = """<html><body><div id='page'>
<form id='f' onsubmit='return go(event)'>
<input name='a' value='x'><button type='submit'>Submit application</button>
</form></div><script>
window.__submits = Number(sessionStorage.getItem('n') || 0);
function go(e) {{
  e.preventDefault();
  window.__submits += 1; sessionStorage.setItem('n', window.__submits);
  {after}
  return false;
}}
</script></body></html>"""


async def _two_submits(page_html: str, tmp_path: Path, *, pause_s: float = 0.0):
    page_file = tmp_path / "form.html"
    page_file.write_text(page_html, encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(page_file.as_uri())
            first = await submit_application(page)
            if pause_s:
                await page.wait_for_timeout(pause_s * 1000)
            second = await submit_application(page, after_doubt=True)
            clicks = await page.evaluate("Number(sessionStorage.getItem('n') || 0)")
            return first, second, clicks
        finally:
            await browser.close()


@pytest.fixture
def short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(submit_mod, "_CONFIRM_POLL_S", 0.05)
    monkeypatch.setattr(submit_mod, "_CONFIRM_WAIT_S", 0.2)
    # Late pages change at 2 s: far past the first wait, well inside the settle.
    monkeypatch.setattr(submit_mod, "_SETTLE_WAIT_S", 4.0)


async def test_a_late_confirmation_settles_the_second_submit_without_a_click(
    short_waits: None, tmp_path: Path
) -> None:
    # The ATS confirms 2 s after the click, past the first wait. Before the
    # fix, the second Submit clicked again: a second application.
    html = _COUNTING_FORM.format(
        after="setTimeout(function(){document.getElementById('page').innerHTML="
        "'<h2>Application received</h2>';}, 2000);"
    )
    first, second, clicks = await _two_submits(html, tmp_path)

    assert first.unconfirmed and not first.confirmed
    assert second.confirmed and not second.unconfirmed
    assert clicks == 1


async def test_an_already_applied_page_counts_as_sent(short_waits: None, tmp_path: Path) -> None:
    html = _COUNTING_FORM.format(
        after="setTimeout(function(){document.getElementById('page').innerHTML="
        "'<p>You have already applied to this job.</p>';}, 2000);"
    )
    first, second, clicks = await _two_submits(html, tmp_path)

    assert first.unconfirmed
    assert second.confirmed
    assert clicks == 1


async def test_a_page_that_moved_on_is_never_clicked_again(
    short_waits: None, tmp_path: Path
) -> None:
    # The form is gone and nothing says sent or not sent: the 2nd Submit must
    # not hunt for another button; the outcome stays unconfirmed.
    html = _COUNTING_FORM.format(
        after="document.getElementById('page').innerHTML="
        "'<p>Processing</p><button type=\"button\">Apply</button>';"
    )
    first, second, clicks = await _two_submits(html, tmp_path)

    assert first.unconfirmed
    assert second.unconfirmed and not second.confirmed
    assert not second.clicked
    assert clicks == 1


async def test_an_untouched_form_is_clicked_again(short_waits: None, tmp_path: Path) -> None:
    # The page ignored the first click entirely: same form, same values, no
    # message, for the whole settle wait. Only then is a 2nd click safe.
    html = _COUNTING_FORM.format(after="")
    first, second, clicks = await _two_submits(html, tmp_path)

    assert first.unconfirmed
    assert second.clicked
    assert clicks == 2


async def test_a_click_that_raises_after_landing_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch, short_waits: None, tmp_path: Path
) -> None:
    # Playwright can raise from a click that already reached the page (the
    # target navigates or closes mid-call). Before the fix that read "not
    # clicked", which invites a second application.
    from playwright.async_api import Error as PlaywrightError

    real_click = submit_mod._click

    async def click_then_raise(locator) -> None:
        await real_click(locator)
        raise PlaywrightError("Target page, context or browser has been closed")

    monkeypatch.setattr(submit_mod, "_click", click_then_raise)
    html = _COUNTING_FORM.format(
        after="document.getElementById('page').innerHTML='<p>Processing</p>';"
    )
    page_file = tmp_path / "form.html"
    page_file.write_text(html, encoding="utf-8")
    outcome = await _submit(page_file.as_uri())

    assert outcome.clicked
    assert outcome.unconfirmed
    assert not outcome.confirmed


async def test_the_confirm_wait_is_wall_time_not_a_count_of_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2026-10-16 CI, macOS: each poll also reads the page, and the loop counted
    # only the poll step, so a "0.2 s" wait ran past 2 s on a slow runner.
    import asyncio
    import time

    async def slow_observe(_page: object) -> object:
        await asyncio.sleep(0.3)
        return object()

    class _Page:
        url = "about:blank"

        async def wait_for_timeout(self, ms: float) -> None:
            await asyncio.sleep(ms / 1000)

    monkeypatch.setattr(submit_mod, "observe", slow_observe)
    monkeypatch.setattr(submit_mod, "classify", lambda _obs: set())
    monkeypatch.setattr(submit_mod, "_CONFIRM_POLL_S", 0.05)
    started = time.monotonic()
    verdict, _url = await submit_mod._read_outcome(_Page(), 0.2)  # type: ignore[arg-type]
    assert verdict == "silent"
    assert time.monotonic() - started < 0.8
