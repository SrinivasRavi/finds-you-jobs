"""Covers: the chat agent's browser tools (audit-2026-10-14.md section 8.11, part B).

Against REAL Chrome: the real app and broker, the referral surface on a local
fixture page, each call through the chat dispatcher as the agent or as the user.
The agent reads, types and chooses. A click, an upload or a new URL can carry
something out of the app, so each waits for the user's OK."""

from __future__ import annotations

from pathlib import Path

import pytest

from sidecar.app.chat import dispatch
from sidecar.app.registry import networker_ops
from sidecar.packages.referral_outreach import referral_surface_slug

from ._support import AUTH

pytest.importorskip("playwright.async_api")

_PAGE = """<html><head><title>fixture</title></head><body><form>
<label for="n">Name</label><input id="n">
<label for="s">Colour</label><select id="s"><option>Red</option><option>Blue</option></select>
<label for="f">CV</label><input id="f" type="file">
<label for="p">Secret</label><input id="p" type="password">
<button type="button" onclick="document.title = 'clicked:' +
  document.getElementById('n').value">Send now</button>
</form></body></html>"""


@pytest.fixture
def page_app(app_client, tmp_path: Path):  # noqa: ANN001
    app, client = app_client
    # The in-app browser is Referral Outreach's; it stays shut until the user opts in.
    shut = dispatch.call("browser_page", {})
    assert shut["status"] == 409 and "Referral Outreach" in shut["error"], shut
    client.post("/api/settings", headers=AUTH, json={"voyager_risk_marker_on": True})
    fixture = tmp_path / "page.html"
    fixture.write_text(_PAGE)
    provider = networker_ops.SURFACE_PROVIDER
    assert provider is not None
    try:
        surface = provider(referral_surface_slug())
    except Exception as exc:  # pragma: no cover, CI without any Chromium
        pytest.skip(f"no Chromium to launch: {exc}")
    surface.set_geometry(1280, 800, 1.0).result(timeout=10)  # what the view sends
    surface.navigate(fixture.as_uri()).result(timeout=30)
    return app, client, surface, tmp_path


def _id(elements: str, needle: str) -> str:
    return next(line.split()[0] for line in elements.splitlines() if needle in line)


def _title(surface) -> str:  # noqa: ANN001
    return surface.evaluate("document.title").result(timeout=10)


def test_the_agent_drives_the_page_and_a_click_waits_for_the_user(page_app) -> None:  # noqa: ANN001
    _app, client, surface, _ = page_app
    read = dispatch.call("browser_page", {})
    assert read["status"] == 200, read
    elements = read["data"]["elements"]
    name, colour, secret = (
        _id(elements, f"label={k!r}") for k in ("Name", "Colour", "Secret")
    )
    send = _id(elements, "Send now")

    typed = dispatch.call("browser_type", {"body": {"element_id": name, "text": "Ada"}})
    assert typed["data"]["ok"], typed
    chose = dispatch.call("browser_choose", {"body": {"element_id": colour, "option": "Blue"}})
    assert chose["data"]["ok"], chose
    password = dispatch.call("browser_type", {"body": {"element_id": secret, "text": "x"}})
    assert not password["data"]["ok"] and "password" in password["data"]["note"]

    # The agent's click comes back as a card that names the button, and nothing ran.
    asked = dispatch.call("browser_click", {"body": {"element_id": send}})
    assert asked["needs_confirmation"] and "'Send now'" in asked["reason"], asked
    assert _title(surface) == "fixture"
    # The user's OK runs the same call.
    clicked = dispatch.call("browser_click", {"body": {"element_id": send}}, who="user")
    assert clicked["data"]["ok"], clicked
    assert _title(surface) == "clicked:Ada"

    text = dispatch.call("browser_page_text", {})
    assert "Send now" in text["data"]["text"]
    stale = dispatch.call("browser_type", {"body": {"element_id": "e999", "text": "x"}})
    assert stale["status"] == 409 and "read it again" in stale["error"]


def test_the_agent_uploads_the_master_resume_as_a_pdf(page_app) -> None:  # noqa: ANN001
    _app, client, surface, _ = page_app
    saved = client.post("/api/profile", headers=AUTH, json={"resume_markdown": "# Ada\n\nWork."})
    assert saved.status_code == 200
    cv = _id(dispatch.call("browser_page", {})["data"]["elements"], "label='CV'")

    body = {"element_id": cv, "document_id": "master_resume"}
    # A page can send a file the moment it is picked, so the agent asks first.
    asked = dispatch.call("browser_upload", {"body": body})
    assert asked["needs_confirmation"] and "master resume" in asked["reason"], asked
    assert surface.evaluate("document.getElementById('f').files.length").result(timeout=10) == 0
    up = dispatch.call("browser_upload", {"body": body}, who="user")
    assert up["data"]["ok"], up
    assert surface.evaluate("document.getElementById('f').files[0].name").result(
        timeout=10
    ) == "master-resume.pdf"
    missing = dispatch.call(
        "browser_upload", {"body": {"element_id": cv, "document_id": "nope"}}, who="user"
    )
    assert missing["status"] == 404


def test_navigate_waits_for_the_user_and_refuses_local_urls(
    page_app, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    _app, client, _surface, tmp_path = page_app
    other = tmp_path / "other.html"
    other.write_text("<title>other</title>")

    # A URL can carry data out, so it asks even with every autopilot switch on.
    client.post("/api/settings", headers=AUTH,
                json={"autopilot_invite": True, "autopilot_dm": True, "autopilot_apply": True})
    asked = dispatch.call("browser_navigate", {"body": {"url": "https://example.com/?q=x"}})
    assert asked["needs_confirmation"] and "Open https://example.com/?q=x" in asked["reason"]
    local = dispatch.call("browser_navigate", {"body": {"url": "http://127.0.0.1/"}}, who="user")
    assert local["status"] == 422

    monkeypatch.setenv("FYJ_APPLY_DEV", "1")
    went = dispatch.call("browser_navigate", {"body": {"url": other.as_uri()}}, who="user")
    assert went["data"]["url"] == other.as_uri(), went
