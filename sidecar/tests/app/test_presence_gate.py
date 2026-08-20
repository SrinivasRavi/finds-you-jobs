"""Covers: the presence gate (invariant 3, Our Claim 9, as revised 2026-08-14).

LinkedIn traffic runs only when the user asked for it: presence IS the
`user_initiated` marker that only an explicit click sets, so a background timer
or scheduler can never start a gated run. The maintainer removed the first live
version's viewer-attached and surface-visible signals the evening they shipped
(they gated on attention, not presence); the Browser tab is observability, never
a requirement, so a send runs identically from any tab.

ZERO live LinkedIn, ZERO real browser: drivers are fakes.
"""

from __future__ import annotations

import pytest

from sidecar.app.registry import networker_ops as ops
from sidecar.app.registry.presence_gate import PresenceAbsent, decide_presence

from ..modules.networker.fakes import FakeVoyagerDriver
from .test_networker_ops_n3 import (  # noqa: F401 — reuse the seeded-DB fixtures
    DISCOVER_ROWS,
    Wired,
    _ctx,
    _nn,
    _seed,
    wired,
)

# --- the pure decision -----------------------------------------------------


def test_present_when_user_initiated() -> None:
    assert decide_presence(user_initiated=True).present is True


def test_absent_when_not_user_initiated() -> None:
    verdict = decide_presence(user_initiated=False)
    assert verdict.present is False
    assert "user-initiated" in verdict.reason


# --- the gate wired into the op paths --------------------------------------


def _seeded_contact(wired: Wired) -> str:  # noqa: F811
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired)
    with wired.db.repos() as repos:
        sarah = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/sarah-tan"))
        return sarah.id


def test_send_refuses_when_not_user_initiated(wired: Wired) -> None:  # noqa: F811
    """A send with no `user_initiated` marker (a background caller) refuses
    before touching the wire."""
    cid = _seeded_contact(wired)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver()
    with pytest.raises(PresenceAbsent):
        # No user_initiated marker on the snapshot.
        ops.send_entrypoint(
            _ctx(wired.db, "send", {"contact_id": cid}, stamp_user_initiated=False)
        )


def test_send_allows_when_user_initiated(wired: Wired) -> None:  # noqa: F811
    """A user-clicked send goes through and writes its OutreachLog — no viewer,
    no visibility, no Browser tab involved (the 2026-08-14 revision)."""
    cid = _seeded_contact(wired)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(
        connection_result={"op": "send-connection", "ok": True, "sent": True, "status": "sent"},
    )
    out = ops.send_entrypoint(
        _ctx(wired.db, "send", {
            "contact_id": cid, "message": "Hi Sarah!", "user_initiated": True,
        })
    )
    assert out.result_ref is not None and out.result_ref["sent"] is True
    with wired.db.repos() as repos:
        assert len(repos.outreach_logs.list_for_contact(cid)) == 1


def test_discover_refuses_when_not_user_initiated(wired: Wired) -> None:  # noqa: F811
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    with pytest.raises(PresenceAbsent):
        ops.discover_entrypoint(
            _ctx(
                wired.db, "discover", {"company": "Northline"},
                stamp_user_initiated=False,
            )
        )


def test_discover_allows_when_user_initiated(wired: Wired) -> None:  # noqa: F811
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired)
    out = ops.discover_entrypoint(
        _ctx(wired.db, "discover", {"company": "Northline", "user_initiated": True})
    )
    assert out.result_ref is not None
