"""Covers: US-NW-12 / FR-NW-15 — the contact-status sync engine.

  a. Sent → Accepted (now 1st-degree, our message last)
  b. Sent → Engagement (now 1st-degree, their message last)
  c. Accepted → Engagement (their message becomes last)
  d. → Ghosted (engagement quiet / sent-stall past the configurable windows)
  - Converted is never auto-touched; a recent MANUAL move is never overridden;
  - the disabled toggle / disconnected session no-op cleanly (zero LinkedIn).

ZERO live LinkedIn traffic: every probe goes through FakeVoyagerDriver (the
`DRIVER_FACTORY` seam). The wire stays cold.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from sidecar.app.db import Database
from sidecar.app.db.base import now_utc
from sidecar.app.registry import OperationContext
from sidecar.app.registry import contact_sync_op as cs
from sidecar.modules.networker.types import NetworkerError, ProbeResult

from ..modules.networker.fakes import FakeVoyagerDriver

# `migrated_db` is auto-discovered from tests/app/conftest.py (no import needed).


def _nn[T](value: T | None) -> T:
    assert value is not None
    return value


def _probe(**raw: Any) -> dict:
    base = {
        "op": "contact-sync", "ok": True, "degree": None, "is_first_degree": False,
        "last_message_direction": None, "last_message_at": None,
    }
    base.update(raw)
    return base


@pytest.fixture
def db(migrated_db: Database) -> Iterator[Database]:  # noqa: F811
    """A migrated DB with Referral Outreach ON + a valid LinkedIn session (the
    sync gate open). The driver seam is restored on teardown."""
    original = cs.DRIVER_FACTORY
    with migrated_db.repos() as repos:
        repos.preferences.update(voyager_risk_marker_on=True)
        repos.linkedin_session.update(status="valid")
    try:
        yield migrated_db
    finally:
        cs.DRIVER_FACTORY = original


def _ctx(db: Database, snap: dict | None = None, *, events: list | None = None) -> OperationContext:
    publish = (lambda e: events.append(e)) if events is not None else None
    with db.repos() as repos:
        op = repos.operations.create("contact_sync", snap or {})
        op_id = op.id
    return OperationContext(
        kind="contact_sync", input_snapshot=snap or {}, db=db,
        operation_id=op_id, publish=publish,
    )


def _make_contact(db: Database, *, status: str, **fields: Any) -> str:
    with db.repos() as repos:
        c = repos.contacts.create(
            f"https://www.linkedin.com/in/{fields.pop('slug', 'jane-doe')}",
            name="Jane Doe", current_company="Northline", connection_status=status,
            **fields,
        )
        return c.id


def _status(db: Database, contact_id: str) -> str:
    with db.repos() as repos:
        return _nn(repos.contacts.get(contact_id)).connection_status


def _inject(probe: dict) -> FakeVoyagerDriver:
    drv = FakeVoyagerDriver(contact_sync_result=probe)
    cs.DRIVER_FACTORY = lambda tier: drv
    return drv


# --- gating ----------------------------------------------------------------


def test_disabled_toggle_noops(db: Database) -> None:
    with db.repos() as repos:
        repos.preferences.update(voyager_risk_marker_on=False)
    drv = _inject(_probe(degree=1, is_first_degree=True))
    _make_contact(db, status="sent", sent_at=now_utc())
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _nn(out.result_ref)["synced"] == 0
    assert _nn(out.result_ref)["skipped"] == "networking_disabled"
    assert drv.calls == []  # zero LinkedIn traffic


def test_no_valid_session_noops(db: Database) -> None:
    with db.repos() as repos:
        repos.linkedin_session.update(status="expired")
    drv = _inject(_probe(degree=1, is_first_degree=True))
    _make_contact(db, status="sent", sent_at=now_utc())
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _nn(out.result_ref)["skipped"] == "no_valid_session"
    assert drv.calls == []


# --- transitions a–d -------------------------------------------------------


def test_sent_to_accepted(db: Database) -> None:  # (a)
    cid = _make_contact(db, status="sent", sent_at=now_utc())
    _inject(_probe(degree=1, is_first_degree=True, last_message_direction="me"))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "accepted"
    with db.repos() as repos:
        assert _nn(repos.contacts.get(cid)).accepted_at is not None


def test_sent_to_engagement(db: Database) -> None:  # (b)
    cid = _make_contact(db, status="sent", sent_at=now_utc())
    _inject(_probe(degree=1, is_first_degree=True, last_message_direction="them"))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "engagement"


def test_accepted_to_engagement(db: Database) -> None:  # (c)
    cid = _make_contact(db, status="accepted", accepted_at=now_utc())
    _inject(_probe(degree=1, is_first_degree=True, last_message_direction="them"))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "engagement"


def test_engagement_to_ghosted_honors_setting(db: Database) -> None:  # (d)
    old = now_utc() - timedelta(days=20)
    cid = _make_contact(db, status="engagement", accepted_at=old)
    # last message 20 days ago; default engagement window is 14 → ghosted.
    _inject(_probe(degree=1, is_first_degree=True, last_message_direction="them",
                   last_message_at=old.timestamp()))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "ghosted"


def test_engagement_stays_when_recent(db: Database) -> None:
    cid = _make_contact(db, status="engagement", accepted_at=now_utc())
    _inject(_probe(degree=1, is_first_degree=True, last_message_direction="them",
                   last_message_at=now_utc().timestamp()))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "engagement"


def test_sent_to_ghosted_honors_setting(db: Database) -> None:  # (d, sent path)
    # Custom short window: sent 5 days ago, window 3 → ghosted.
    with db.repos() as repos:
        prefs = repos.preferences.get_or_create()
        repos.preferences.update(ui_state={**(prefs.ui_state or {}),
                                           "lifecycle": {"sent_ghosted_days": 3}})
    cid = _make_contact(db, status="sent", sent_at=now_utc() - timedelta(days=5))
    _inject(_probe(degree=2, is_first_degree=False))  # still not connected
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "ghosted"


def test_sent_not_ghosted_before_window(db: Database) -> None:
    cid = _make_contact(db, status="sent", sent_at=now_utc() - timedelta(days=2))
    _inject(_probe(degree=2, is_first_degree=False))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "sent"


# --- manual wins -----------------------------------------------------------


def test_converted_never_auto_moved(db: Database) -> None:
    cid = _make_contact(db, status="converted", accepted_at=now_utc())
    drv = _inject(_probe(degree=1, is_first_degree=True, last_message_direction="them"))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "converted"
    assert drv.calls == []  # converted is not even in the syncable set


def test_recent_manual_move_not_overridden(db: Database) -> None:
    cid = _make_contact(
        db, status="sent", sent_at=now_utc(),
        profile_payload={"status_meta": {"source": "manual",
                                         "changed_at": now_utc().isoformat()}},
    )
    drv = _inject(_probe(degree=1, is_first_degree=True, last_message_direction="me"))
    cs.contact_sync_entrypoint(_ctx(db))
    # A probe WOULD promote to accepted, but the fresh manual move wins.
    assert _status(db, cid) == "sent"
    assert drv.calls == []  # not even probed — gentle + manual-respecting


def test_stale_manual_move_is_synced(db: Database) -> None:
    cid = _make_contact(
        db, status="sent", sent_at=now_utc(),
        profile_payload={"status_meta": {
            "source": "manual",
            "changed_at": (now_utc() - timedelta(days=10)).isoformat()}},
    )
    _inject(_probe(degree=1, is_first_degree=True, last_message_direction="me"))
    cs.contact_sync_entrypoint(_ctx(db))
    # Manual move older than the cooldown → auto is free to advance it.
    assert _status(db, cid) == "accepted"


def test_batch_failure_does_not_kill_tick(db: Database) -> None:
    cid = _make_contact(db, status="sent", sent_at=now_utc())
    drv = FakeVoyagerDriver(raise_on="contact_sync_states",
                            error=NetworkerError("voyager", "driver crashed"))
    cs.DRIVER_FACTORY = lambda tier: drv
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "sent"  # unchanged, no crash
    ref = _nn(out.result_ref)
    assert ref["synced"] == 0
    # The failure is SAID, not hidden behind a clean zero (2026-08-15 honesty).
    assert ref["stopped"] == "batch_failed"
    assert ref["unprobed"] == 1


def test_per_contact_probe_failure_does_not_kill_sweep(db: Database) -> None:
    """A 403/404-class miss on one contact skips THAT contact only — the rest
    of the one-session sweep still probes and transitions (error isolation),
    and the result_ref counts the skip instead of reporting a clean sweep."""
    now = now_utc()
    broken = _make_contact(db, status="sent", sent_at=now, slug="gone-404",
                           last_touched_at=now - timedelta(days=3))
    fine = _make_contact(db, status="sent", sent_at=now, slug="jane-doe",
                         last_touched_at=now - timedelta(days=2))
    drv = FakeVoyagerDriver(contact_sync_batch_results={
        "gone-404": _probe(ok=False, error="probe_failed",
                           reason="profile inaccessible (404)"),
        "jane-doe": _probe(degree=1, is_first_degree=True,
                           last_message_direction="me"),
    })
    cs.DRIVER_FACTORY = lambda tier: drv
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, broken) == "sent"  # skipped, unchanged
    assert _status(db, fine) == "accepted"  # the sweep went on
    ref = _nn(out.result_ref)
    assert ref["synced"] == 1
    assert ref["failed"] == 1
    assert "stopped" not in ref  # a skip is not a stop


def test_auth_error_mid_sweep_stops_and_reports_honestly(db: Database) -> None:
    """A 401 is a dead SESSION, not one bad contact — every later probe would
    401 identically, so the sweep legitimately stops there. What must not
    happen is the stop hiding: the result_ref names it and sizes the untouched
    tail (the 2026-08-14 boots logged the stop but reported a clean sweep)."""
    now = now_utc()
    first = _make_contact(db, status="sent", sent_at=now, slug="alpha",
                          last_touched_at=now - timedelta(days=3))
    dead = _make_contact(db, status="sent", sent_at=now, slug="bravo",
                         last_touched_at=now - timedelta(days=2))
    tail = _make_contact(db, status="sent", sent_at=now, slug="charlie",
                         last_touched_at=now - timedelta(days=1))
    drv = FakeVoyagerDriver(contact_sync_batch_results={
        "alpha": _probe(degree=1, is_first_degree=True, last_message_direction="me"),
        "bravo": _probe(ok=False, error="auth_error",
                        reason="Messaging API returned 401 Unauthorized."),
        "charlie": _probe(degree=1, is_first_degree=True),
    })
    cs.DRIVER_FACTORY = lambda tier: drv
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, first) == "accepted"
    assert _status(db, dead) == "sent"   # rotated, never transitioned
    assert _status(db, tail) == "sent"   # never probed
    ref = _nn(out.result_ref)
    assert ref["synced"] == 1
    assert ref["stopped"] == "auth_error"
    assert ref["unprobed"] == 1  # charlie — first in line next tick


# --- wire-cold: a real reply payload moves the card (the 2026-08-15 defect) --


def _parse_reply_payload(last_sender: str) -> tuple[str | None, float | None]:
    """Run the REAL parsers on the GraphQL messenger-inbox fixture (the live
    endpoint's captured shape — see the GPL probe tests): the sweep's inbox
    map, then the contact's direction joined by their fsd_profile urn."""
    from sidecar.packages.referral_outreach.upstream.voyager import (
        inbox_direction_for,
        parse_inbox_last_messages,
    )

    from ..packages.referral_outreach.test_contact_sync_probe import (
        SELF_MEMBER,
        TARGET,
        TARGET_MEMBER,
        _conversation,
        _inbox_payload,
        _message,
    )

    sender = TARGET_MEMBER if last_sender == "target" else SELF_MEMBER
    payload = _inbox_payload([_conversation(TARGET_MEMBER, [
        _message(sender, 1_700_000_002_000),  # newest first
        _message(SELF_MEMBER, 1_700_000_001_000),
    ])])
    direction, ts, found, _text, _from_name = inbox_direction_for(
        parse_inbox_last_messages(payload), TARGET
    )
    assert found is True
    return direction, ts


def test_incoming_reply_payload_moves_accepted_to_engagement(db: Database) -> None:
    """The full chain, wire-cold: an inbox payload where the CONTACT sent
    last → the inbox parsers read ("them", ts) → `decide_transition`
    (accepted) says engagement → the sync entrypoint moves the card. This is
    the exact live failure of 2026-08-14: the reply arrived, two full sweeps
    ran, the card never left Accepted."""
    direction, ts = _parse_reply_payload("target")
    assert direction == "them"
    assert ts == 1_700_000_002.0

    now = now_utc()
    decision = cs.decide_transition(
        "accepted",
        _p(degree=1, is_first_degree=True,
           last_message_direction=direction, last_message_at=ts),
        sent_at=None, accepted_at=now, settings=_SETTINGS, now=now,
    )
    assert decision.new_status == "engagement"

    cid = _make_contact(db, status="accepted", accepted_at=now)
    _inject(_probe(degree=1, is_first_degree=True,
                   last_message_direction=direction, last_message_at=ts))
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "engagement"
    assert _nn(out.result_ref)["transitions"] == {"accepted->engagement": 1}
    with db.repos() as repos:
        meta = (_nn(repos.contacts.get(cid)).profile_payload or {}).get("status_meta")
        assert _nn(meta)["source"] == "auto"


def test_our_own_last_message_payload_keeps_accepted(db: Database) -> None:
    """The control: the same payload shape with OUR message last reads `me`
    and the card stays in Accepted (no false Engagement)."""
    direction, ts = _parse_reply_payload("self")
    assert direction == "me"

    cid = _make_contact(db, status="accepted", accepted_at=now_utc())
    _inject(_probe(degree=1, is_first_degree=True,
                   last_message_direction=direction, last_message_at=ts))
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "accepted"
    assert _nn(out.result_ref)["transitions"] == {}


# --- last_thread_message display persistence (the card's REAL last message) --


def _thread_snapshot(db: Database, contact_id: str) -> dict | None:
    with db.repos() as repos:
        payload = _nn(repos.contacts.get(contact_id)).profile_payload or {}
        return payload.get("last_thread_message")


def test_sync_persists_incoming_thread_message_to_profile_payload(db: Database) -> None:
    """A probed `them` message lands in `profile_payload.last_thread_message`
    (text, direction, iso timestamp, the contact's thread display name) — the
    display source the card/modal attribute — and composes with the
    `status_meta` stamp the same transition writes."""
    cid = _make_contact(db, status="accepted", accepted_at=now_utc())
    _inject(_probe(degree=1, is_first_degree=True,
                   last_message_direction="them",
                   last_message_at=1_700_000_002.0,
                   last_message_text="Happy to refer you, send the link!",
                   last_message_from="Jane Doe"))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "engagement"
    snap = _nn(_thread_snapshot(db, cid))
    assert snap["text"] == "Happy to refer you, send the link!"
    assert snap["direction"] == "them"
    assert snap["from_name"] == "Jane Doe"
    assert snap["at"] is not None and snap["at"].startswith("2023-11-14")
    with db.repos() as repos:
        payload = _nn(repos.contacts.get(cid)).profile_payload or {}
        # Both riders of the JSON column survived the same write.
        assert _nn(payload.get("status_meta"))["source"] == "auto"


def test_sync_persists_our_own_message_without_a_transition(db: Database) -> None:
    """A `me` probe with no column move still refreshes the display snapshot
    (the else-branch write path)."""
    cid = _make_contact(db, status="accepted", accepted_at=now_utc())
    _inject(_probe(degree=1, is_first_degree=True,
                   last_message_direction="me",
                   last_message_at=1_700_000_003.0,
                   last_message_text="Following up on my earlier note.",
                   last_message_from="Jane Doe"))
    cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "accepted"
    snap = _nn(_thread_snapshot(db, cid))
    assert snap["direction"] == "me"
    assert snap["text"] == "Following up on my earlier note."


def test_probe_without_a_thread_keeps_the_previous_snapshot(db: Database) -> None:
    """No thread on the fetched inbox page ⇒ the earlier sync's snapshot stays
    — a read miss must never blank the card."""
    previous = {"text": "Old but real", "direction": "them",
                "at": "2023-11-14T00:00:02+00:00", "from_name": "Jane Doe"}
    cid = _make_contact(db, status="accepted", accepted_at=now_utc(),
                        profile_payload={"last_thread_message": previous})
    _inject(_probe(degree=1, is_first_degree=True))  # honest nulls
    cs.contact_sync_entrypoint(_ctx(db))
    assert _thread_snapshot(db, cid) == previous


# --- the DTO the kanban/card reads (thread snapshot first, log fallback) -----


def test_contact_dto_prefers_the_thread_snapshot(db: Database) -> None:
    """`_contact_dto` serves the REAL thread message once a sync stored it:
    a `them` snapshot yields direction `them` + the sender's display name,
    even when a later OutreachLog of ours exists."""
    from sidecar.app.api.routes import _contact_dto

    cid = _make_contact(db, status="engagement", accepted_at=now_utc(),
                        profile_payload={"last_thread_message": {
                            "text": "Happy to refer you!", "direction": "them",
                            "at": "2023-11-14T00:00:02+00:00",
                            "from_name": "Jane Doe",
                        }})
    with db.repos() as repos:
        repos.outreach_logs.create(
            contact_id=cid, job_id=None, channel="dm",
            body_sent="Our own older ask", outcome="sent",
        )
        dto = _contact_dto(repos, _nn(repos.contacts.get(cid)))
    assert dto.last_message == "Happy to refer you!"
    assert dto.last_message_direction == "them"
    assert dto.last_message_from == "Jane Doe"
    assert dto.last_message_at is not None


def test_contact_dto_falls_back_to_the_outreach_log_pre_sync(db: Database) -> None:
    """No synced snapshot yet ⇒ the latest OutreachLog fills in, honestly
    attributed as OURS (direction `me`, no from_name) — a card with outreach
    history is never blank."""
    from sidecar.app.api.routes import _contact_dto

    cid = _make_contact(db, status="sent", sent_at=now_utc())
    with db.repos() as repos:
        repos.outreach_logs.create(
            contact_id=cid, job_id=None, channel="connection_note",
            body_sent="Hi Jane, I'd love to connect.", outcome="sent",
        )
        dto = _contact_dto(repos, _nn(repos.contacts.get(cid)))
    assert dto.last_message == "Hi Jane, I'd love to connect."
    assert dto.last_message_direction == "me"
    assert dto.last_message_from is None


# --- one-session batch sweep ----------------------------------------------


def test_sweep_makes_one_batch_call_for_n_contacts(db: Database) -> None:
    """The whole sweep is ONE driver call (one browser session) — never one
    launch/quit cycle per contact (2026-08-04 live observation: 5 contacts =
    5 Chromium cycles, ~106 s)."""
    now = now_utc()
    cids = [
        _make_contact(db, status="sent", sent_at=now, slug=f"person-{i}",
                      last_touched_at=now - timedelta(days=9 - i))
        for i in range(3)
    ]
    drv = _inject(_probe(degree=1, is_first_degree=True, last_message_direction="me"))
    out = cs.contact_sync_entrypoint(_ctx(db))
    batch_calls = [c for c in drv.calls if c[0] == "contact_sync_states"]
    single_calls = [c for c in drv.calls if c[0] == "contact_sync"]
    assert len(batch_calls) == 1  # exactly ONE session for N contacts
    assert single_calls == []
    assert len(batch_calls[0][1]) == 3  # all three pids rode the one call
    for cid in cids:
        assert _status(db, cid) == "accepted"
    assert _nn(out.result_ref)["synced"] == 3


def test_rate_limited_mid_sweep_stops_remaining_probes(db: Database) -> None:
    """The first RateLimited stops the batch (section 0.4): the rate-limited
    contact and every contact after it are left untouched (not transitioned,
    not rotated) — the refused row learned nothing, so it stays first in line
    for the next tick (2026-08-16: the old rotate-on-refusal made every
    budget-refused Sync press churn one card's cursor for zero traffic)."""
    now = now_utc()
    first = _make_contact(db, status="sent", sent_at=now, slug="alpha",
                          last_touched_at=now - timedelta(days=3))
    throttled = _make_contact(db, status="sent", sent_at=now, slug="bravo",
                              last_touched_at=now - timedelta(days=2))
    untouched = _make_contact(db, status="sent", sent_at=now, slug="charlie",
                              last_touched_at=now - timedelta(days=1))
    drv = FakeVoyagerDriver(contact_sync_batch_results={
        "alpha": _probe(degree=1, is_first_degree=True, last_message_direction="me"),
        "bravo": _probe(ok=False, error="rate_limited",
                        reason="LinkedIn returned HTTP 429"),
        "charlie": _probe(degree=1, is_first_degree=True, last_message_direction="me"),
    })
    cs.DRIVER_FACTORY = lambda tier: drv
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, first) == "accepted"
    assert _status(db, throttled) == "sent"  # never transitioned
    assert _status(db, untouched) == "sent"  # never reached
    with db.repos() as repos:
        # The refused row is NOT rotated — it stays first in line …
        assert _nn(repos.contacts.get(throttled)).last_touched_at == now - timedelta(days=2)
        # … and the tail behind the stop was left completely untouched.
        assert _nn(repos.contacts.get(untouched)).last_touched_at == now - timedelta(days=1)
    assert _nn(out.result_ref)["synced"] == 1
    assert _nn(out.result_ref)["stopped"] == "rate_limited"


# --- pure decision function ------------------------------------------------

_SETTINGS = {"engagement_ghosted_days": 14, "sent_ghosted_days": 21,
             "contact_purge_days": 60, "trashed_jobs_purge_days": 7,
             "archived_applications_purge_days": 30, "contact_sync_cadence_hours": 12}


def _p(**raw: Any) -> ProbeResult:
    return ProbeResult(public_identifier="x", **raw)


def test_decide_sent_pending_no_move_before_window() -> None:
    now = now_utc()
    d = cs.decide_transition("sent", _p(degree=2), sent_at=now - timedelta(days=5),
                             accepted_at=None, settings=_SETTINGS, now=now)
    assert d.new_status is None


def test_decide_accepted_stall_ghosts_after_window() -> None:
    now = now_utc()
    d = cs.decide_transition("accepted", _p(is_first_degree=True, last_message_direction="me"),
                             sent_at=None, accepted_at=now - timedelta(days=30),
                             settings=_SETTINGS, now=now)
    assert d.new_status == "ghosted"


# --- the read-budget redesign (2026-08-16): cached urns + thread-only probes -


def test_full_probe_caches_the_resolved_urn(db: Database) -> None:
    """A full probe's `target_urn` lands in `profile_payload.fsd_urn` — the
    key that makes every later sweep's thread question free for this row."""
    cid = _make_contact(db, status="sent", sent_at=now_utc())
    _inject(_probe(degree=1, is_first_degree=True, last_message_direction="me",
                   target_urn="urn:li:fsd_profile:ACtest"))
    cs.contact_sync_entrypoint(_ctx(db))
    with db.repos() as repos:
        payload = _nn(repos.contacts.get(cid)).profile_payload or {}
        assert payload["fsd_urn"] == "urn:li:fsd_profile:ACtest"
        # The same write carried the transition stamp — one composed payload.
        assert _nn(payload.get("status_meta"))["source"] == "auto"


def test_accepted_row_with_cached_urn_probes_thread_only(db: Database) -> None:
    """An accepted/engagement row with a cached urn rides the unmetered
    thread-only pass (the driver call says so), still transitions on a reply,
    and keeps its stored degree (the probe answers degree None)."""
    cid = _make_contact(
        db, status="accepted", accepted_at=now_utc(),
        connection_degree=1, is_first_degree=True,
        profile_payload={"fsd_urn": "urn:li:fsd_profile:ACtest"},
    )
    drv = _inject(_probe(degree=None, is_first_degree=False,
                         last_message_direction="them",
                         last_message_at=now_utc().timestamp(),
                         last_message_text="All good here as well",
                         last_message_from="Jane Doe",
                         target_urn="urn:li:fsd_profile:ACtest"))
    out = cs.contact_sync_entrypoint(_ctx(db))
    batch_call = next(c for c in drv.calls if c[0] == "contact_sync_states")
    assert batch_call[3] == ("jane-doe",)  # the thread-only set the driver saw
    assert _status(db, cid) == "engagement"  # the reply still moves the card
    with db.repos() as repos:
        row = _nn(repos.contacts.get(cid))
        assert row.connection_degree == 1  # stored degree stands (not blanked)
        snap = (row.profile_payload or {}).get("last_thread_message")
        assert _nn(snap)["text"] == "All good here as well"
    assert _nn(out.result_ref)["synced"] == 1


def test_sent_row_with_cached_urn_still_gets_a_full_probe(db: Database) -> None:
    """A pending invite's open question is the DEGREE — a cached urn must not
    demote it to a thread-only probe."""
    _make_contact(db, status="sent", sent_at=now_utc(),
                  profile_payload={"fsd_urn": "urn:li:fsd_profile:ACtest"})
    drv = _inject(_probe(degree=1, is_first_degree=True,
                         last_message_direction="me"))
    cs.contact_sync_entrypoint(_ctx(db))
    batch_call = next(c for c in drv.calls if c[0] == "contact_sync_states")
    assert batch_call[3] == ()  # nobody rode the thread-only pass


def test_frozen_engagement_row_refreshes_display_without_a_move(db: Database) -> None:
    """A manual-frozen accepted/engagement row with a cached urn is no longer
    held stale for the whole cooldown: it rides the unmetered thread-only
    pass for its DISPLAY snapshot while its manual column choice stands (a
    probe that would move it is suppressed)."""
    cid = _make_contact(
        db, status="accepted", accepted_at=now_utc(),
        profile_payload={
            "fsd_urn": "urn:li:fsd_profile:ACtest",
            "status_meta": {"source": "manual",
                            "changed_at": now_utc().isoformat()},
        },
    )
    drv = _inject(_probe(degree=None, is_first_degree=False,
                         last_message_direction="them",
                         last_message_at=now_utc().timestamp(),
                         last_message_text="Just saw your note — yes!",
                         last_message_from="Jane Doe",
                         target_urn="urn:li:fsd_profile:ACtest"))
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert _status(db, cid) == "accepted"  # the manual choice stands …
    snap = _nn(_thread_snapshot(db, cid))
    assert snap["text"] == "Just saw your note — yes!"  # … the card is fresh
    assert _nn(out.result_ref)["transitions"] == {}
    assert _nn(out.result_ref)["frozen"] == 0  # probed (display-only), not skipped
    batch_call = next(c for c in drv.calls if c[0] == "contact_sync_states")
    assert batch_call[3] == ("jane-doe",)


def test_frozen_row_without_urn_still_rotates_unprobed(db: Database) -> None:
    """The pre-redesign freeze behaviour survives where a probe would be
    metered: a frozen row with NO cached urn rotates without a probe."""
    _make_contact(
        db, status="accepted", accepted_at=now_utc(),
        profile_payload={"status_meta": {"source": "manual",
                                         "changed_at": now_utc().isoformat()}},
    )
    drv = _inject(_probe(degree=1, is_first_degree=True))
    out = cs.contact_sync_entrypoint(_ctx(db))
    assert drv.calls == []  # nothing eligible → no driver, no browser
    assert _nn(out.result_ref)["frozen"] == 1


def test_budget_refused_press_leaves_every_row_in_place(db: Database) -> None:
    """The 2026-08-15 live shuffle: a spent read budget refused the first
    probe, the engine rotated that one card, and every Sync press reordered
    the board. A refused sweep now leaves last_touched_at untouched on every
    row and surfaces the stop."""
    now = now_utc()
    a = _make_contact(db, status="sent", sent_at=now, slug="alpha",
                      last_touched_at=now - timedelta(days=2))
    b = _make_contact(db, status="sent", sent_at=now, slug="bravo",
                      last_touched_at=now - timedelta(days=1))
    drv = FakeVoyagerDriver(contact_sync_batch_results={
        "alpha": _probe(ok=False, error="cap_or_backoff",
                        reason="profile-view budget spent"),
    })
    cs.DRIVER_FACTORY = lambda tier: drv
    out = cs.contact_sync_entrypoint(_ctx(db))
    with db.repos() as repos:
        assert _nn(repos.contacts.get(a)).last_touched_at == now - timedelta(days=2)
        assert _nn(repos.contacts.get(b)).last_touched_at == now - timedelta(days=1)
    ref = _nn(out.result_ref)
    assert ref["synced"] == 0
    assert ref["stopped"] == "cap_or_backoff"
    assert ref["unprobed"] == 1
