# voyager_py/tests/test_broker_lane_connect_note.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Phase 3: the verbatim GPL connect-with-note flow runs on the broker's lane,
against a local fixture (`fixtures/profiles/invite_classic_modal.html`), never
linkedin.com.

The note is typed and Send clicked on the surface thread, and the textarea holds
the exact message. `run_browser` is called from a worker thread, as the runner
does. Anchors: FR-NW-03; the broker's `run_on_lane` (section 5.4)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from sidecar.app.browser import BrowserBroker, BrowserLaunchError  # noqa: E402
from sidecar.packages.referral_outreach.upstream import session as session_mod  # noqa: E402
from sidecar.packages.referral_outreach.upstream.actions import _click_with_note  # noqa: E402
from sidecar.packages.referral_outreach.upstream.session import AccountSession  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures" / "profiles"

# A de-headlessed Chrome UA, so the broker never spends a throwaway launch to
# resolve one (the pattern `test_broker.py` uses).
FAKE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

NOTE = "Hi, hoping to connect about the backend referral."


async def test_connect_note_flow_runs_on_the_broker_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The human-pace page-jitter waits (5-8 s each) are realism, not correctness;
    # zero them so the test is quick. The per-keystroke typing and the click
    # approach still run for real on the lane — that is the point being proven.
    monkeypatch.setattr(session_mod, "random_sleep", lambda *_a, **_k: None)

    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        user_agent_resolver=lambda: FAKE_UA,
        geometry_timeout=5.0,
    )
    # The surface slug is a runtime argument; the broker never learns the vendor.
    surface = broker.surface(session_mod.SURFACE_SLUG)
    try:
        try:
            await surface.wait_ready(timeout_seconds=60)
        except BrowserLaunchError as exc:  # pragma: no cover — CI without Chrome
            pytest.skip(f"real Chrome unavailable: {exc}")

        # Point the surface at the connect-with-note fixture, on its own lane
        # (bypassing the display-geometry gate the broker's `navigate` enforces —
        # `page.goto` needs no geometry, and this is a local file).
        fixture_uri = (_FIXTURES / "invite_classic_modal.html").as_uri()
        await asyncio.wrap_future(
            surface.run_on_lane(
                lambda s: s.page.goto(fixture_uri, wait_until="domcontentloaded")
            )
        )

        # A broker-backed session: it launches NO browser of its own — every
        # page action runs on this surface's lane, with the surface's page bound.
        session = AccountSession(surface_provider=lambda _slug: surface)

        # Drive the verbatim GPL connect-with-note flow through the lane, from a
        # worker thread (like the operation runner), so the `.result()` inside
        # `run_browser` blocks that thread and never the serving loop.
        outcome = await asyncio.to_thread(
            session.run_browser, lambda: _click_with_note(session, NOTE)
        )
        assert outcome == "with_note"

        # The note textarea holds the exact composed message, and the send fired
        # with that value — both read back off the surface page over its lane.
        textarea_value = await asyncio.wrap_future(
            surface.run_on_lane(
                lambda s: s.page.evaluate("document.querySelector('textarea').value")
            )
        )
        assert textarea_value == NOTE
        sent_value = await asyncio.wrap_future(
            surface.run_on_lane(lambda s: s.page.evaluate("window.__sent"))
        )
        assert sent_value == NOTE

        # Detaching the session must leave the broker-owned surface alive.
        session.close()
        assert not surface.is_dead
    finally:
        broker.shutdown()
