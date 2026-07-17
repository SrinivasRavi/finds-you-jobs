# finds-you-jobs — AGPL-3.0-only.
"""Feasibility spike for the observation core (roadmap commit 12 Slice A).

Drives the Skyvern-derived observation seam (`observe`) against a static local
job-application form in real headless Chromium — ZERO network — and proves the
core contract: a viewport screenshot, an interactive-element tree that includes
labelled inputs / select+options / file upload / submit AND an input inside a
same-origin iframe (frame walking), opaque per-observation element ids mapped to
non-empty upstream unique ids, a non-empty compact HTML render carrying the
form's labels, and fresh element ids on every observation.

No agent loop, no actions, no submit — observation only.
"""

from pathlib import Path

from playwright.async_api import async_playwright

from sidecar.packages.jobapplier.observe import Observation, observe

FIXTURES = Path(__file__).parent / "fixtures"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _fixture_url(name: str) -> str:
    return (FIXTURES / name).as_uri()


def _by_label(obs: Observation, needle: str):
    return [e for e in obs.elements if needle.lower() in e.label.lower()]


async def _observe_fixture(name: str):
    """Open the fixture in headless Chromium (file://, no network) and observe."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(_fixture_url(name))
            # Zero-network guarantee: the page and every frame are local.
            assert page.url.startswith("file:")
            for frame in page.frames:
                assert frame.url.startswith(("file:", "about:")), frame.url
            obs = await observe(page)
            # A second observation after a DOM mutation, to prove ids are
            # reassigned per observation against the CURRENT tree.
            await page.evaluate(
                """() => {
                    const form = document.getElementById('application');
                    const label = document.createElement('label');
                    label.setAttribute('for', 'phone');
                    label.textContent = 'Phone number';
                    const input = document.createElement('input');
                    input.id = 'phone';
                    input.name = 'phone';
                    input.type = 'tel';
                    form.appendChild(label);
                    form.appendChild(input);
                }"""
            )
            obs2 = await observe(page)
        finally:
            await browser.close()
    return obs, obs2


async def test_observation_spike_static_application_form() -> None:
    obs, obs2 = await _observe_fixture("application_form.html")

    # -- screenshot is a real PNG -------------------------------------------
    assert obs.screenshot_png.startswith(_PNG_MAGIC)
    assert len(obs.screenshot_png) > 0

    # -- url / title round-trip ---------------------------------------------
    assert obs.url.startswith("file:")
    assert obs.url.endswith("application_form.html")
    assert obs.title == "Apply — Senior Widget Engineer at Globex"

    # -- the form's controls are all observed -------------------------------
    assert _by_label(obs, "Full name"), "name input missing"
    assert _by_label(obs, "Email address"), "email input missing"

    tags = {e.tag for e in obs.elements}
    assert "select" in tags, "select not observed"
    assert any(
        e.tag == "input" and e.attributes.get("type") == "file" for e in obs.elements
    ), "file input not observed"
    assert any(
        e.tag == "button" and "submit" in e.text.lower() for e in obs.elements
    ), "submit button not observed"

    # -- frame walking: the iframe's labelled input is observed -------------
    assert obs.frame_count >= 2, obs.frame_count
    iframe_inputs = [e for e in obs.elements if e.frame_index >= 1]
    assert iframe_inputs, "no element observed inside the iframe"
    assert _by_label(obs, "Portfolio URL"), "iframe input label missing"

    # -- element ids: opaque, unique, mapped to non-empty unique ids --------
    element_ids = [e.element_id for e in obs.elements]
    assert len(element_ids) == len(set(element_ids)), "element ids are not unique"
    assert element_ids == [f"e{i}" for i in range(1, len(obs.elements) + 1)]
    assert all(e.unique_id for e in obs.elements), "an element has an empty unique_id"

    # -- compact HTML render carries the form's labels ----------------------
    assert obs.element_tree_html
    for label_text in ("Full name", "Email address", "Portfolio URL"):
        assert label_text in obs.element_tree_html, label_text
    # the select's options survive into the render
    assert "LinkedIn" in obs.element_tree_html

    # -- per-observation contract: fresh ids on the next observation --------
    # The appended "Phone number" field is present only in the second
    # observation, and ids restart from e1 against the current tree — proving
    # ids are assigned per observation, not carried over.
    assert not _by_label(obs, "Phone number")
    assert _by_label(obs2, "Phone number"), "new field not observed on re-observe"
    assert len(obs2.elements) == len(obs.elements) + 1
    obs2_ids = [e.element_id for e in obs2.elements]
    assert obs2_ids == [f"e{i}" for i in range(1, len(obs2.elements) + 1)]
    assert obs2_ids[0] == "e1"
