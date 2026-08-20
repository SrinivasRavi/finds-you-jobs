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
        "</body></html>"
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
        "</form></body></html>"
    )

    outcome = await _submit(page_file.as_uri())

    assert outcome.clicked
    assert not outcome.confirmed
    assert "no confirmation within" in outcome.note
