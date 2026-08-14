# voyager_py/tests/test_referral_workflow_e2e.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Phase-9 acceptance (fixture tier): the COMPLETE referral workflow, wire cold.

Chains the three Networker stages end to end and stops at the per-action confirm,
with zero linkedin.com, zero real LLM, and zero account:

1. DISCOVER — the module orchestrator (`networker.discover`) against a fake
   `VoyagerDriver` that returns one fixture 2nd-degree contact. No network.
2. DRAFT   — the one LLM stage (`networker.draft`) against a fake engine that
   returns a fixed grounded connection-note. No real LLM.
3. COMPOSE — the drafted note is carried into the verbatim GPL connect-with-note
   flow (`actions.find_and_click_connect` → `actions._click_with_note`), run on
   the CORE browser broker's serialized surface lane against a LOCAL fixture
   profile shaped like LinkedIn's connect-with-note DOM (its markup matches
   `actions.CONNECT_SELECTORS`). Connect is clicked, the note composer opens, and
   the drafted message is human-typed into it.

Then it asserts the composer holds the EXACT drafted message and the surface can
be screenshotted (the text is really on screen), and it STOPS: the fixture's
"Send invitation" is INERT — it copies the composed note into a JS variable and
posts NOWHERE. The real send (`networker.send` → the driver → the network) is
never invoked; that is the per-action-confirm boundary this fixture tier
deliberately stops short of, reserved for the maintainer's live tier (one real
login, one real send).

The synchronous worker→lane bridge is exercised authentically: `run_browser` is
invoked from a worker thread (`asyncio.to_thread`, as the operation runner does),
so it `.result()`s the surface's `concurrent.futures.Future` off the serving loop.

Earlier phases proved the pieces — Phase 3 the connect-with-note flow on the
broker lane, Phase 8 the paste-URL → kanban → confirm UI; Phase 9 chains discover
+ draft + compose into ONE deterministic, hermetic CI proof. It touches no
`upstream/*.py` (it imports the verbatim GPL actions/session unchanged), so no
fork edit was needed for this phase (see `provenance.md`).

Anchors: US-REF-01/03/04, FR-NW-03 (the referral ask rides in the connect note);
the broker's `run_on_lane` hook (section 5.4, "one account, one lane").
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from sidecar.app.browser import BrowserBroker, BrowserLaunchError  # noqa: E402
from sidecar.modules.networker import discover as net_discover  # noqa: E402
from sidecar.modules.networker import draft as net_draft  # noqa: E402
from sidecar.packages.referral_outreach.upstream import session as session_mod  # noqa: E402
from sidecar.packages.referral_outreach.upstream.actions import (  # noqa: E402
    _click_with_note,
    find_and_click_connect,
)
from sidecar.packages.referral_outreach.upstream.session import AccountSession  # noqa: E402

from ...modules.networker.fakes import FakeEngine, FakeVoyagerDriver  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
SURFACE_SLUG = "referral-e2e"

# A de-headlessed Chrome UA, so the broker never spends a throwaway launch to
# resolve one (the pattern `test_broker_lane_connect_note.py` uses).
FAKE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# One 2nd-degree contact from discovery → cold → connection-request-with-note
# (the connect-with-note path the fixture models).
_DISCOVERED = {
    "op": "discover",
    "ok": True,
    "contacts": [
        {
            "public_identifier": "dana-reyes",
            "full_name": "Dana Reyes",
            "headline": "Staff Software Engineer at Northline",
            "current_title": "Staff Software Engineer",
            "current_company": "Northline",
            "url": "https://www.linkedin.com/in/dana-reyes/",
            "connection_degree": 2,
        }
    ],
}

# A realistic JD (≥ 80 chars, so `resolve_job` reads it as JD text, not a path).
_JOB = (
    "Senior Backend Engineer at Northline. Own the payments platform: Go "
    "services, distributed systems, and high-throughput ledgers. 5+ years."
)

# The seeker's master profile — the sole grounding evidence the draft is allowed.
_MASTER_MD = (
    "# Jane Seeker\n\nBackend engineer, 6 years in Go and payments/ledger systems.\n"
)

# The message the fake engine drafts. Carried verbatim into the composer; kept to
# one short cold-note sentence so the real per-keystroke typing stays quick.
_MESSAGE = "Hi Dana, hoping to connect about the backend engineer referral at Northline."

_DRAFT_OUTPUT = (
    "===MESSAGE===\n"
    f"{_MESSAGE}\n"
    "===NOTES===\n"
    "- Grounded in the master profile's backend + payments experience.\n"
    "- No claim beyond the master profile.\n"
)


def _drive_connect_with_note(session: AccountSession, message: str) -> str:
    """Click Connect, open the note composer, human-type `message` — the verbatim
    GPL flow, run inside the lane where the surface's Playwright is greenlet-bound.
    Returns `_click_with_note`'s outcome ("with_note" on the note path)."""
    find_and_click_connect(session.page)
    return _click_with_note(session, message)


async def test_referral_workflow_composes_the_connect_note_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --- 1) DISCOVER (no network): the module orchestrator against a fake driver.
    driver = FakeVoyagerDriver(discover_result=_DISCOVERED)
    discovered = net_discover("Northline", driver=driver, limit=10)
    assert [c.public_identifier for c in discovered.contacts] == ["dana-reyes"]
    contact = discovered.contacts[0]
    # Discovery tagged it cold (2nd degree), so the send channel is a connection
    # note — the connect-with-note path.
    assert contact.warmth.value == "cold"
    assert not contact.is_first_degree
    # The fake driver only discovered — no send method was ever reached.
    assert [call[0] for call in driver.calls] == ["discover"]
    assert driver.closed

    # --- 2) DRAFT (no real LLM): the one LLM stage against a fake engine.
    drafted = net_draft(
        contact, _JOB, master_md=_MASTER_MD, engine=FakeEngine(raw=_DRAFT_OUTPUT)
    )
    assert drafted.channel.value == "connection_note"
    assert drafted.message == _MESSAGE

    # The human-pace page-jitter waits (5-8 s each) are realism, not correctness;
    # zero them so the test is quick. The per-keystroke typing and the clicks still
    # run for real on the lane — that is the point being proven.
    monkeypatch.setattr(session_mod, "random_sleep", lambda *_a, **_k: None)

    # --- 3) COMPOSE on the broker lane, against the LOCAL connect-with-note fixture.
    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        user_agent_resolver=lambda: FAKE_UA,
        geometry_timeout=5.0,
    )
    # The surface slug is a runtime argument; the broker never learns the vendor.
    surface = broker.surface(SURFACE_SLUG)
    try:
        try:
            await surface.wait_ready(timeout_seconds=60)
        except BrowserLaunchError as exc:  # pragma: no cover — CI without Chrome
            pytest.skip(f"real Chrome unavailable: {exc}")

        # Point the surface at the connect-with-note fixture on its own lane
        # (bypassing the display-geometry gate `navigate` enforces — a `page.goto`
        # of a local file needs no geometry, and this is not the network).
        fixture_uri = (_FIXTURES / "referral_connect_note.html").as_uri()
        await asyncio.wrap_future(
            surface.run_on_lane(
                lambda s: s.page.goto(fixture_uri, wait_until="domcontentloaded")
            )
        )

        # A broker-backed session: it launches NO browser of its own — every page
        # action runs on this surface's lane, with the surface's page bound.
        session = AccountSession(surface_provider=lambda _slug: surface)

        # Drive the verbatim GPL connect-with-note flow through the lane, from a
        # worker thread (like the operation runner), so the `.result()` inside
        # `run_browser` blocks that thread and never the serving loop.
        outcome = await asyncio.to_thread(
            session.run_browser,
            lambda: _drive_connect_with_note(session, drafted.message),
        )
        assert outcome == "with_note"

        # The composer holds the EXACT drafted message — it rendered correctly...
        note_value = await asyncio.wrap_future(
            surface.run_on_lane(
                lambda s: s.page.evaluate("document.querySelector('textarea').value")
            )
        )
        assert note_value == drafted.message

        # ...and the text is really on screen (the surface can be captured).
        shot = await asyncio.wrap_future(surface.screenshot())
        assert isinstance(shot, bytes) and len(shot) > 0

        # STOP at the confirm: the fixture's "Send invitation" is INERT — it copied
        # the composed note into a JS variable and posted NOWHERE. No linkedin.com
        # was touched; the real send behind the per-action confirm is never invoked.
        sent_value = await asyncio.wrap_future(
            surface.run_on_lane(lambda s: s.page.evaluate("window.__sent"))
        )
        assert sent_value == drafted.message

        # Detaching the session must leave the broker-owned surface alive.
        session.close()
        assert not surface.is_dead
    finally:
        broker.shutdown()
