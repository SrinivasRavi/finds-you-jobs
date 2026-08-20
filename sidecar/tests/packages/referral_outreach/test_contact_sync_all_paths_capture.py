# voyager_py/tests/test_contact_sync_all_paths_capture.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""`actions.get_contact_sync_state` end-to-end, wire-cold: the sweep-shared
GraphQL inbox read + the ALL-PATHS probe capture (FR-NW-15, 2026-08-15).

The live 2026-08-14 sweep returned `synced: 5, transitions: {}` with ZERO
capture files (success-only capture, dead endpoint underneath). These tests pin
the replacement contract end to end: the probe answers the last-message
question from the sweep's ONE inbox request (session-cached — a 2-contact
sweep fires exactly 1 messaging request), a real reply reads `them` (rule c:
Accepted → Engagement upstream), our own last message reads `me` (the card
stays), a contact without a thread gets honest nulls, and with
`FYJ_LINKEDIN_CAPTURE_DIR` set ONE redacted JSON lands per probed contact on
EVERY path — success, urn-less skip, profile failure, throttle (which
propagates to the worker's backoff instead of dying in the blanket except).

Zero live LinkedIn traffic — profile reads are monkeypatched, /me and the
GraphQL inbox run against the in-memory fake page with SYNTHETIC fixtures.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sidecar.packages.referral_outreach.upstream import actions
from sidecar.packages.referral_outreach.upstream.client import PlaywrightLinkedinAPI
from sidecar.packages.referral_outreach.upstream.errors import (
    ProfileInaccessibleError,
    RateLimited,
)

from .test_contact_sync_probe import (
    SELF_MEMBER,
    TARGET,
    TARGET_MEMBER,
    _conversation,
    _inbox_payload,
    _message,
)
from .test_last_message_client import GRAPHQL, ME_PAYLOAD, REPLY_INBOX, _FakeSession

PID = "some-contact"

# SELF sent last — the live-verified control thread (reads `me`, card stays).
OURS_LAST_INBOX = _inbox_payload([_conversation(TARGET_MEMBER, [
    _message(SELF_MEMBER, 1_700_000_003_000),
    _message(TARGET_MEMBER, 1_700_000_001_000),
])])


def _session(routes: dict[str, tuple[int, dict]]) -> Any:
    """An in-memory stand-in for AccountSession (typed Any: the probe only
    touches page/context/ensure_browser/ensure_linkedin_origin)."""
    return _FakeSession(routes)


def _stub_profile(monkeypatch, urn: str | None, degree: int | None = 1) -> None:
    monkeypatch.setattr(
        PlaywrightLinkedinAPI, "get_profile",
        lambda self, public_identifier=None, profile_url=None: (
            {"urn": urn, "connection_degree": degree}, {}
        ),
    )


def _one_capture(capture_dir) -> dict:
    files = sorted(capture_dir.glob("contact-sync-probe-*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text())


def test_reply_reads_them_with_every_stage_captured(tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    _stub_profile(monkeypatch, TARGET)
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (200, REPLY_INBOX),
    })
    state = actions.get_contact_sync_state(session, PID)
    assert state == {
        "degree": 1, "is_first_degree": True,
        "last_message_direction": "them", "last_message_at": 1_700_000_002.0,
        # The display pair rides the same read: their text, their name.
        "last_message_text": "Thanks for reaching out, happy to chat!",
        "last_message_from": "Alex Doe",
        # The join key the host caches for later unmetered thread-only probes.
        "target_urn": TARGET,
    }
    doc = _one_capture(capture_dir)
    # Identity-free: the vanity slug and every member id are placeholders —
    # and the display pair the probe now RETURNS (text + name) never enters
    # the capture either.
    blob = json.dumps(doc)
    assert PID not in blob and TARGET_MEMBER not in blob and SELF_MEMBER not in blob
    assert "happy to chat" not in blob and "Alex" not in blob
    assert doc["public_identifier"].startswith("ID_")
    assert doc["profile"]["ok"] is True and doc["profile"]["error"] is None
    assert doc["profile"]["degree"] == 1
    assert doc["profile"]["target_urn"].startswith("urn:li:fsd_profile:ID_")
    msg = doc["messaging"]
    assert msg["skipped"] is None
    assert msg["inbox"]["status"] == 200 and msg["inbox"]["ok"] is True
    assert msg["inbox"]["conversations"] == 1 and msg["inbox"]["one_to_one"] == 1
    assert msg["thread_found"] is True
    assert doc["parsed"] == {"direction": "them", "sent_at": 1_700_000_002.0}


def test_our_own_last_message_reads_me(tmp_path, monkeypatch):
    """The muddied-test-profile control: SELF sent last → `me` → upstream
    decide_transition keeps the card in Accepted (no move is the CORRECT
    outcome)."""
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    _stub_profile(monkeypatch, TARGET)
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (200, OURS_LAST_INBOX),
    })
    state = actions.get_contact_sync_state(session, PID)
    assert state["last_message_direction"] == "me"
    assert state["last_message_at"] == 1_700_000_003.0
    doc = _one_capture(capture_dir)
    assert doc["messaging"]["thread_found"] is True
    assert doc["parsed"]["direction"] == "me"


def test_two_contact_sweep_fires_one_messaging_request(tmp_path, monkeypatch):
    """The account-safety redesign, end to end: 2 probes on ONE session share
    one inbox read — the second capture says `cached: true`."""
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    _stub_profile(monkeypatch, TARGET)
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (200, REPLY_INBOX),
    })
    actions.get_contact_sync_state(session, PID)
    actions.get_contact_sync_state(session, "another-contact")
    graphql_calls = [u for u in session.page.fetched if GRAPHQL in u]
    assert len(graphql_calls) == 1
    files = sorted(capture_dir.glob("contact-sync-probe-*.json"))
    assert len(files) == 2
    doc1 = json.loads(files[0].read_text())
    doc2 = json.loads(files[1].read_text())
    assert doc1["messaging"]["inbox"]["cached"] is False
    assert doc2["messaging"]["inbox"]["cached"] is True
    assert doc2["payload"] is None  # the shape rides only on the fetching probe


def test_no_thread_on_the_page_is_honest_nulls(tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    _stub_profile(monkeypatch, TARGET, degree=2)
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (200, _inbox_payload([])),  # inbox page without their thread
    })
    state = actions.get_contact_sync_state(session, PID)
    assert state == {
        "degree": 2, "is_first_degree": False,
        "last_message_direction": None, "last_message_at": None,
        "last_message_text": None, "last_message_from": None,
        "target_urn": TARGET,
    }
    doc = _one_capture(capture_dir)
    assert doc["messaging"]["thread_found"] is False
    assert doc["parsed"] == {"direction": None, "sent_at": None}


def test_urnless_profile_skips_messaging_and_still_captures(tmp_path, monkeypatch):
    """target_urn None → no inbox read at all (no fetch of any kind) — and the
    capture says so instead of the probe leaving no trace."""
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    _stub_profile(monkeypatch, None, degree=2)
    session = _session({})  # any fetch would raise — none may happen
    state = actions.get_contact_sync_state(session, PID)
    assert state == {
        "degree": 2, "is_first_degree": False,
        "last_message_direction": None, "last_message_at": None,
        "last_message_text": None, "last_message_from": None,
        "target_urn": None,
    }
    assert session.page.fetched == []
    doc = _one_capture(capture_dir)
    assert doc["profile"]["ok"] is True and doc["profile"]["target_urn"] is None
    assert doc["messaging"]["skipped"] == "no_target_urn"
    assert doc["messaging"]["inbox"] is None
    assert doc["parsed"] == {"direction": None, "sent_at": None}


def test_profile_failure_captures_the_stage_and_propagates(tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))

    def _boom(self, public_identifier=None, profile_url=None):
        raise ProfileInaccessibleError(f"{public_identifier} (HTTP 403)")

    monkeypatch.setattr(PlaywrightLinkedinAPI, "get_profile", _boom)
    session = _session({})
    with pytest.raises(ProfileInaccessibleError):
        actions.get_contact_sync_state(session, PID)
    doc = _one_capture(capture_dir)
    assert doc["profile"]["ok"] is False
    assert doc["profile"]["error"] == "ProfileInaccessibleError"
    assert doc["messaging"]["inbox"] is None
    assert doc["parsed"] is None


def test_inbox_throttle_propagates_and_captures(tmp_path, monkeypatch):
    """A 429 on the inbox read reaches the worker (backoff + sweep stop) and
    the capture names it — it used to die in the blanket `except Exception`."""
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    _stub_profile(monkeypatch, TARGET)
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (429, {}),
    })
    with pytest.raises(RateLimited):
        actions.get_contact_sync_state(session, PID)
    doc = _one_capture(capture_dir)
    inbox = doc["messaging"]["inbox"]
    assert inbox["status"] == 429 and inbox["error"] == "RateLimited"
    assert doc["parsed"] is None


def test_capture_off_by_default_writes_nothing_and_probe_unchanged(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("FYJ_LINKEDIN_CAPTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _stub_profile(monkeypatch, TARGET)
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (200, REPLY_INBOX),
    })
    state = actions.get_contact_sync_state(session, PID)
    assert state["last_message_direction"] == "them"
    assert list(tmp_path.iterdir()) == []


# --- the unmetered thread-only probe (2026-08-16) ----------------------------


def test_thread_only_probe_reads_the_thread_without_a_profile_fetch(
    tmp_path, monkeypatch
):
    """`get_contact_thread_state` answers from the one inbox read alone: no
    `get_profile` runs (it is stubbed to explode), degree comes back None
    (not read this sweep), and the capture marks the urn as cached — still
    identity- and body-free."""
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))

    def _boom(self, public_identifier=None, profile_url=None):
        raise AssertionError("a thread-only probe must never read the profile")

    monkeypatch.setattr(PlaywrightLinkedinAPI, "get_profile", _boom)
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (200, REPLY_INBOX),
    })
    state = actions.get_contact_thread_state(session, PID, TARGET)
    assert state == {
        "degree": None, "is_first_degree": False,
        "last_message_direction": "them", "last_message_at": 1_700_000_002.0,
        "last_message_text": "Thanks for reaching out, happy to chat!",
        "last_message_from": "Alex Doe",
        "target_urn": TARGET,
    }
    doc = _one_capture(capture_dir)
    blob = json.dumps(doc)
    assert PID not in blob and TARGET_MEMBER not in blob and SELF_MEMBER not in blob
    assert "happy to chat" not in blob and "Alex" not in blob
    assert doc["profile"]["cached_urn"] is True
    assert doc["profile"]["degree"] is None
    assert doc["profile"]["target_urn"].startswith("urn:li:fsd_profile:ID_")
    assert doc["messaging"]["thread_found"] is True
    assert doc["parsed"] == {"direction": "them", "sent_at": 1_700_000_002.0}


def test_thread_only_probe_throttle_propagates(tmp_path, monkeypatch):
    """A 429 on the inbox read propagates from the thread-only probe exactly
    like the full probe — the worker's backoff/sweep-stop signal."""
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (429, {}),
    })
    with pytest.raises(RateLimited):
        actions.get_contact_thread_state(session, PID, TARGET)
    doc = _one_capture(capture_dir)
    assert doc["messaging"]["inbox"]["status"] == 429
    assert doc["parsed"] is None


def test_thread_only_probe_missing_thread_is_honest_nulls(tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    session = _session({
        "/voyager/api/me": (200, ME_PAYLOAD),
        GRAPHQL: (200, _inbox_payload([])),
    })
    state = actions.get_contact_thread_state(session, PID, TARGET)
    assert state["last_message_direction"] is None
    assert state["last_message_text"] is None
    assert state["degree"] is None
    doc = _one_capture(capture_dir)
    assert doc["messaging"]["thread_found"] is False
