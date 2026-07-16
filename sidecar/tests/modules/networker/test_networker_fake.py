"""Networker orchestration — discover / draft / send — driven by the in-memory
fakes (no voyager subprocess, no LLM, no network).

Covers US-REF-01/02/03/04/10, FR-NW-03/04, FR-REF-02: audience tagging at
discovery, grounded draft assembly, warm/cold routing, caps surfaced verbatim,
verbatim error propagation, dry-run states, and driver teardown in finally.
"""

from __future__ import annotations

import pytest

from sidecar.modules.networker.networker import discover, draft, probe, quota, send
from sidecar.modules.networker.types import (
    Audience,
    Channel,
    Contact,
    NetworkerError,
    Warmth,
)

from .fakes import BoomEngine, FakeEngine, FakeVoyagerDriver

# resolve_job requires ≥ 80 chars for raw text — a realistic JD blurb.
JOB = (
    "Senior Backend Engineer at Acme. Build distributed payment systems in Go and "
    "Kubernetes. 5+ years backend experience required. Remote, India."
)


# ── discover ──────────────────────────────────────────────────────
def test_discover_tags_each_contact_and_closes_driver():
    drv = FakeVoyagerDriver(discover_result={
        "ok": True,
        "contacts": [
            {"public_identifier": "em-jane", "current_title": "Engineering Manager",
             "connection_degree": 2, "full_name": "Jane Doe"},
            {"public_identifier": "peer-bob", "current_title": "Staff Engineer",
             "connection_degree": 1, "full_name": "Bob Roe"},
            {"public_identifier": "rec-sue", "current_title": "Technical Recruiter",
             "connection_degree": 3},
        ],
    })
    result = discover("Acme", JOB, driver=drv, limit=10)

    assert result.company == "Acme"
    assert [c.public_identifier for c in result.contacts] == ["em-jane", "peer-bob", "rec-sue"]
    jane, bob, sue = result.contacts
    assert jane.audience is Audience.HM and jane.warmth is Warmth.COLD
    assert bob.audience is Audience.PEER and bob.warmth is Warmth.WARM and bob.is_first_degree
    assert sue.audience is Audience.RECRUITER
    assert result.usage.internal_calls == 1
    assert result.usage.usd is None  # zero-LLM
    assert drv.closed is True
    assert drv.calls[0] == ("discover", "Acme", 10, None, 1, False)


def test_discover_empty_company_raises():
    with pytest.raises(NetworkerError):
        discover("", JOB, driver=FakeVoyagerDriver())


def test_discover_dry_run_passthrough():
    drv = FakeVoyagerDriver()
    discover("Acme", JOB, driver=drv, dry_run=True)
    assert drv.calls[0][5] is True  # dry_run forwarded (company_urn + page slot before it)


# ── draft ─────────────────────────────────────────────────────────
def _contact(audience=Audience.HM, degree=2):
    return Contact(public_identifier="jane", full_name="Jane Doe",
                   current_title="Engineering Manager", current_company="Acme",
                   connection_degree=degree, audience=audience)


def test_draft_happy_path_grounded():
    engine = FakeEngine()
    result = draft(_contact(), JOB, master_md="Go, Kubernetes, 6 yrs backend", engine=engine)
    assert result.message.startswith("Hi Jane")
    assert result.audience is Audience.HM
    assert result.warmth is Warmth.COLD
    assert result.channel is Channel.CONNECTION_NOTE
    assert result.char_count == len(result.message)
    assert any("trace to the master" in n for n in result.notes)
    assert result.usage.model == "fake"
    # the master + playbook are actually in the assembled prompt
    _system, user = engine.seen[0]
    assert "Go, Kubernetes" in user
    assert "Hiring Manager" in user  # HM playbook bound


def test_draft_warm_contact_routes_to_dm_channel():
    result = draft(_contact(audience=Audience.PEER, degree=1), JOB,
                   master_md="x" * 100, engine=FakeEngine())
    assert result.warmth is Warmth.WARM
    assert result.channel is Channel.DM


def test_draft_engine_error_propagates_verbatim():
    with pytest.raises(NetworkerError) as exc:
        draft(_contact(), JOB, master_md="x", engine=BoomEngine())
    assert "rate limit" in str(exc.value)


def test_draft_parse_error_on_bad_contract():
    engine = FakeEngine(raw="no contract markers here at all")
    with pytest.raises(NetworkerError) as exc:
        draft(_contact(), JOB, master_md="x", engine=engine)
    assert exc.value.stage == "parse"


def test_draft_missing_public_identifier_raises():
    with pytest.raises(NetworkerError):
        draft(Contact(public_identifier=""), JOB, master_md="x", engine=FakeEngine())


# ── send ──────────────────────────────────────────────────────────
def test_send_warm_routes_to_dm():
    drv = FakeVoyagerDriver(dm_result={"ok": True, "sent": True, "quota": {"daily_remaining": 15}})
    contact = Contact(public_identifier="jane", connection_degree=1)
    result = send("Hi Jane, would you refer me?", contact, driver=drv, tier="new")
    assert result.channel is Channel.DM
    assert result.sent is True
    assert drv.calls[0][0] == "send_dm"
    assert drv.closed is True


def test_send_cold_routes_to_connection_with_note():
    drv = FakeVoyagerDriver()
    contact = Contact(public_identifier="bob", connection_degree=2)
    result = send("Hi Bob, exploring the role at Acme.", contact, driver=drv)
    assert result.channel is Channel.CONNECTION_NOTE
    assert drv.calls[0][0] == "send_connection"
    # the drafted message rides as the connection NOTE (FR-NW-03)
    assert drv.calls[0][2] == "Hi Bob, exploring the role at Acme."


def test_send_surfaces_cap_block_without_raising():
    drv = FakeVoyagerDriver(connection_result={
        "ok": False, "sent": False, "error": "cap_or_backoff",
        "reason": "daily cap reached (15/day, tier=new)",
        "quota": {"daily_remaining": 0},
    })
    contact = Contact(public_identifier="bob", connection_degree=2)
    result = send("hello there friend", contact, driver=drv)
    assert result.sent is False
    assert result.error == "cap_or_backoff"
    assert "daily cap" in result.reason
    assert result.quota == {"daily_remaining": 0}
    assert drv.closed is True


def test_send_verbatim_voyager_error_propagates_and_still_closes():
    drv = FakeVoyagerDriver(raise_on="send_dm",
                            error=NetworkerError("voyager", "voyager_py exited 1: crash"))
    contact = Contact(public_identifier="jane", connection_degree=1)
    with pytest.raises(NetworkerError) as exc:
        send("hi there", contact, driver=drv)
    assert "crash" in str(exc.value)
    assert drv.closed is True  # teardown in finally


def test_send_empty_message_raises():
    with pytest.raises(NetworkerError):
        send("   ", Contact(public_identifier="jane", connection_degree=1),
             driver=FakeVoyagerDriver())


def test_send_dry_run_is_planned_and_sends_nothing():
    drv = FakeVoyagerDriver(dm_result={"ok": True, "dry_run": True, "would_send": True,
                                       "blocked_reason": "", "quota": {"daily_remaining": 15}})
    contact = Contact(public_identifier="jane", connection_degree=1)
    result = send("hi there", contact, driver=drv, dry_run=True)
    assert result.status == "planned"
    assert result.sent is False
    assert drv.calls[0][4] is True  # dry_run forwarded


# ── probe (read-only contact-status sync, FR-NW-15) ───────────────
def test_probe_maps_degree_and_last_message_and_closes():
    drv = FakeVoyagerDriver(contact_sync_result={
        "ok": True, "degree": 1, "is_first_degree": True,
        "last_message_direction": "them", "last_message_at": 1_700_000_000.0,
    })
    result = probe(Contact(public_identifier="jane"), driver=drv)
    assert result.is_first_degree is True and result.degree == 1
    assert result.last_message_direction == "them"
    assert result.last_message_at == 1_700_000_000.0
    assert drv.calls[0] == ("contact_sync", "jane", False)
    assert drv.closed is True


def test_probe_read_miss_leaves_message_fields_empty():
    drv = FakeVoyagerDriver()  # default: degree None, no message
    result = probe(Contact(public_identifier="jane"), driver=drv)
    assert result.degree is None
    assert result.last_message_direction == ""  # no transition signal
    assert result.last_message_at is None


def test_probe_requires_public_identifier():
    with pytest.raises(NetworkerError):
        probe(Contact(public_identifier=""), driver=FakeVoyagerDriver())


# ── quota ─────────────────────────────────────────────────────────
def test_quota_returns_voyager_quota_and_closes():
    drv = FakeVoyagerDriver(quota_result={"ok": True, "quota": {"daily_remaining": 7}})
    out = quota(driver=drv, tier="new")
    assert out["quota"]["daily_remaining"] == 7
    assert drv.calls[0] == ("quota", "new")
    assert drv.closed is True
