# finds-you-jobs — AGPL-3.0-only.
"""Executor vs the custom widgets that dominate real ATS forms (Ashby paired
yes/no buttons, BambooHR button dropdowns, typeahead comboboxes) — real
headless Chromium, a local fixture, zero network. Proves the three behaviours
the live-fill evidence demanded: a select on a non-native dropdown opens the
menu and picks the option, a fill on a combobox commits the matching
suggestion so blur cannot clear it, and a click whose effect was swallowed is
retried instead of blindly reported ok."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from sidecar.packages.jobapplier.actions import Action
from sidecar.packages.jobapplier.executor import Executor, UrlPolicy, _match_option
from sidecar.packages.jobapplier.observe import observe
from sidecar.packages.jobapplier.types import ApplyRequest

FIXTURES = Path(__file__).parent / "fixtures"


def _request(tmp_path: Path) -> ApplyRequest:
    return ApplyRequest(
        run_id="run-w",
        application_id="app-w",
        job_url=(FIXTURES / "custom_widgets.html").as_uri(),
        company="Acme",
        role="Staff Engineer",
        jd_text="Own the monolith.",
        profile_facts={},
        preferences={},
        approved_links=(),
        artifacts=(),
        resume_label="resume",
        screenshot_dir=str(tmp_path / "shots"),
    )


@pytest.fixture
async def widget_page(tmp_path: Path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto((FIXTURES / "custom_widgets.html").as_uri())
        obs = await observe(page)
        executor = Executor(page, _request(tmp_path), UrlPolicy(allow_local=True))
        executor.bind_observation(obs)
        try:
            yield page, obs, executor
        finally:
            await browser.close()


def _eid(obs, **want: str) -> str:
    for e in obs.elements:
        hay = f"{e.label} {e.text} {e.attributes.get('id', '')} {e.attributes.get('name', '')}"
        if all(needle in hay for needle in want.values()):
            return e.element_id
    seen = [(e.element_id, e.label, e.text) for e in obs.elements]
    raise AssertionError(f"no element matches {want}: {seen}")


async def test_select_on_button_dropdown_opens_menu_and_picks(widget_page) -> None:
    page, obs, executor = widget_page
    out = await executor.execute(
        Action(
            tool="select",
            args={"element_id": _eid(obs, id="state-toggle"), "option": "Alaska"},
        )
    )
    assert out.ok, out.note
    assert "Alaska" in out.note
    assert await page.locator("#state-toggle").inner_text() == "State Alaska"
    assert await page.locator("select[name=state]").input_value() == "Alaska"


async def test_select_reports_missing_option_honestly(widget_page) -> None:
    page, obs, executor = widget_page
    out = await executor.execute(
        Action(
            tool="select",
            args={"element_id": _eid(obs, id="state-toggle"), "option": "Maharashtra"},
        )
    )
    assert not out.ok
    assert "not in the menu" in out.note
    # Escape closed the menu so it cannot cover later targets.
    assert await page.locator("#state-menu").is_hidden()


async def test_fill_on_combobox_commits_matching_suggestion(widget_page) -> None:
    page, obs, executor = widget_page
    out = await executor.execute(
        Action(
            tool="fill",
            args={"element_id": _eid(obs, id="loc"), "value": "Mumbai, Maharashtra, India"},
        )
    )
    assert out.ok, out.note
    assert "committed suggestion" in out.note
    # The committed value survives blur — the whole point.
    await page.locator("input[name=notes-after]").focus()
    assert await page.locator("#loc").input_value() == "Mumbai, Maharashtra, India"


async def test_fill_on_combobox_without_match_is_reported(widget_page) -> None:
    page, obs, executor = widget_page
    # "Mumbai" surfaces a suggestion, but the full value matches none of them.
    out = await executor.execute(
        Action(
            tool="fill",
            args={"element_id": _eid(obs, id="loc"), "value": "Mumbai Kingdom of Atlantis"},
        )
    )
    assert not out.ok
    assert "no suggestion matches" in out.note


async def test_click_commits_paired_yes_no_button(widget_page) -> None:
    page, obs, executor = widget_page
    out = await executor.execute(
        Action(tool="click", args={"element_id": _eid(obs, id="sponsor-yes")})
    )
    assert out.ok, out.note
    assert "no observable state change" not in out.note
    assert await page.locator("input[name=sponsor]").is_checked()


async def test_swallowed_click_is_retried(widget_page) -> None:
    page, obs, executor = widget_page
    out = await executor.execute(
        Action(tool="click", args={"element_id": _eid(obs, id="flaky")})
    )
    assert out.ok, out.note
    assert "no observable state change" not in out.note
    assert await page.locator("#flaky").get_attribute("aria-pressed") == "true"


def test_match_option_prefers_exact_then_unique_containment() -> None:
    assert _match_option(["Yes", "No"], "yes") == 0
    assert _match_option(["Alabama", "Alaska"], "Alaska") == 1
    # Unique containment either way.
    assert _match_option(["Master's Degree", "Bachelor's Degree"], "Master's") == 0
    # Ambiguous containment refuses to guess.
    assert _match_option(["Bachelor of Arts", "Bachelor of Science"], "Bachelor") is None
    assert _match_option(["Alabama", "Alaska"], "") is None
    # Typographic apostrophe / punctuation never distinguishes options.
    assert _match_option(["I’m a veteran", "I am not a veteran"], "I'm a veteran") == 0


def test_match_option_prefix_breaks_containment_tie() -> None:
    # "India" is contained in "British Indian Ocean Territory", so containment
    # alone is ambiguous; the exact-prefix tier resolves it.
    countries = ["India", "British Indian Ocean Territory", "Indonesia"]
    assert _match_option(countries, "India") == 0


def test_match_option_buckets_a_number_into_its_range() -> None:
    buckets = ["0-5 years", "6-10 years", "10+ years"]
    assert _match_option(buckets, "9") == 1
    assert _match_option(buckets, "3") == 0
    assert _match_option(buckets, "12") == 2
    # A value inside two overlapping ranges is genuinely ambiguous: refuse.
    assert _match_option(["5 to 10 years", "6 to 10 years"], "7") is None
