# voyager_py/tests/test_connect_note_flow.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""The connection-request-WITH-note flow (`_click_with_note`) against LOCAL
fixture HTML with REAL Chromium — no live LinkedIn. Covers the two invite
layouts LinkedIn ships plus the honest degradation:

  - classic modal  → "Add a note" reveals a <textarea>; note typed, Send clicked
  - SDUI / Premium → the note field (a contenteditable) is present directly, no
    "Add a note" button; note still typed and sent (the 2026-07-13 regression:
    the old code degraded to note-less here and dropped the referral ask)
  - no note field  → degrade to a note-less "Send now" (never dead-end)

Anchors: FR-NW-03 (referral ask rides in the connect note).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from sidecar.packages.referral_outreach.upstream.actions import (  # noqa: E402
    _click_with_note,
    _verify_invite_sent,
)

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


def _session_for(browser, fixture: str):
    page = browser.new_page()
    page.goto((_FIXTURES / fixture).as_uri())
    # A minimal AccountSession stand-in: the note flow only touches .page + .wait().
    return SimpleNamespace(page=page, wait=lambda *a, **k: None)


def test_classic_modal_types_note_and_sends(browser):
    session = _session_for(browser, "invite_classic_modal.html")
    try:
        _click_with_note(session, "Hi — hoping to connect re: a referral.")  # type: ignore[arg-type]
        assert session.page.evaluate("window.__sent") == "Hi — hoping to connect re: a referral."
    finally:
        session.page.close()


def test_sdui_direct_note_field_still_carries_the_note(browser):
    # The regression: SDUI opens the note compose directly (no "Add a note"
    # button). The note MUST still be typed and sent — not silently dropped.
    session = _session_for(browser, "invite_sdui_direct_note.html")
    try:
        _click_with_note(session, "Warm intro request for the backend role.")  # type: ignore[arg-type]
        assert session.page.evaluate("window.__sent") == "Warm intro request for the backend role."
    finally:
        session.page.close()


def test_no_note_field_degrades_to_noteless_send(browser):
    session = _session_for(browser, "invite_no_note_field.html")
    try:
        _click_with_note(session, "This note has nowhere to go.")  # type: ignore[arg-type]
        assert session.page.evaluate("window.__noteless === true")
        # Nothing was typed anywhere — the invite still went out, note-less.
        assert session.page.evaluate("window.__sent === undefined")
    finally:
        session.page.close()


# --- post-send verification (never claim a false "sent") -------------------


def test_verify_invite_sent_true_on_confirmation_toast(browser):
    session = _session_for(browser, "invite_confirmed_toast.html")
    try:
        assert _verify_invite_sent(session, timeout_ms=1500) is True  # type: ignore[arg-type]
    finally:
        session.page.close()


def test_verify_invite_sent_true_on_pending_affordance(browser):
    session = _session_for(browser, "invite_confirmed_pending.html")
    try:
        assert _verify_invite_sent(session, timeout_ms=1500) is True  # type: ignore[arg-type]
    finally:
        session.page.close()


def test_verify_invite_sent_false_when_unconfirmed(browser):
    # No toast, no Pending — the send is NOT confirmed; the caller must fail
    # honestly rather than record a false "sent" (2026-07-13 regression).
    session = _session_for(browser, "invite_unconfirmed.html")
    try:
        assert _verify_invite_sent(session, timeout_ms=1500) is False  # type: ignore[arg-type]
    finally:
        session.page.close()
