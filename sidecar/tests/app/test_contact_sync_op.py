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
    assert _nn(out.result_ref)["synced"] == 0


def test_per_contact_probe_failure_does_not_kill_sweep(db: Database) -> None:
    """A 403/404-class miss on one contact skips THAT contact only — the rest
    of the one-session sweep still probes and transitions (error isolation)."""
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
    assert _nn(out.result_ref)["synced"] == 1


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
    """The first RateLimited stops the batch (§0.4): the rate-limited contact is
    rotated without a transition, and every contact after it is left untouched
    (not probed, not rotated) — first in line for the next tick."""
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
    assert _status(db, throttled) == "sent"  # rotated, never transitioned
    assert _status(db, untouched) == "sent"  # never reached
    with db.repos() as repos:
        # The throttled row rotated (cursor advances past it) …
        assert _nn(repos.contacts.get(throttled)).last_touched_at > now - timedelta(days=1)
        # … while the tail behind the stop was left completely untouched.
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
