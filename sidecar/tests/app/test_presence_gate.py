"""Covers: the presence gate (invariant 3, Our Claim 9).

LinkedIn traffic runs only with the user present. Under a headless streamed
surface, present = a user-initiated run AND an attached screencast viewer AND a
surface that reports itself visible. The gate reads the broker's own viewer
tracking plus `visibility()`, and trusts `visibilityState` only — never
`hasFocus()`, which Playwright's focus emulation makes lie (Our Finding 14).

ZERO live LinkedIn, ZERO real browser: the surface is a fixture whose
`has_viewer` and `visibility()` are set by the test.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future
from typing import Any

import pytest

from sidecar.app.registry import networker_ops as ops
from sidecar.app.registry.presence_gate import (
    PresenceAbsent,
    decide_presence,
    read_surface_presence,
)

from ..modules.networker.fakes import FakeVoyagerDriver
from .test_networker_ops_n3 import (  # noqa: F401 — reuse the seeded-DB fixtures
    DISCOVER_ROWS,
    Wired,
    _ctx,
    _nn,
    _seed,
    wired,
)


class FakeSurface:
    """A stand-in browser surface exposing only what the gate reads. `hasFocus`
    is left True even when hidden — the exact focus-emulation lie the gate must
    ignore (Our Finding 14)."""

    def __init__(
        self,
        *,
        has_viewer: bool = True,
        visibility_state: str = "visible",
    ) -> None:
        self.has_viewer = has_viewer
        self._state = {"visibilityState": visibility_state, "hasFocus": True}

    def visibility(self) -> Future:
        future: Future = Future()
        future.set_result(dict(self._state))
        return future


# --- the pure decision -----------------------------------------------------


def test_present_when_all_three_hold() -> None:
    verdict = decide_presence(
        user_initiated=True, viewer_attached=True, visibility_state="visible"
    )
    assert verdict.present is True


def test_absent_when_not_user_initiated() -> None:
    verdict = decide_presence(
        user_initiated=False, viewer_attached=True, visibility_state="visible"
    )
    assert verdict.present is False
    assert "user-initiated" in verdict.reason


def test_absent_when_no_viewer_attached() -> None:
    verdict = decide_presence(
        user_initiated=True, viewer_attached=False, visibility_state="visible"
    )
    assert verdict.present is False
    assert "viewer" in verdict.reason


def test_absent_when_hidden() -> None:
    verdict = decide_presence(
        user_initiated=True, viewer_attached=True, visibility_state="hidden"
    )
    assert verdict.present is False
    assert "visible" in verdict.reason


# --- reading a live surface ------------------------------------------------


def test_read_surface_hidden_tab_refuses_despite_focus_lie() -> None:
    """A genuinely hidden tab reports visibilityState=hidden yet hasFocus()=True
    (Our Finding 14). The gate must refuse on visibilityState, never be fooled by
    the focus lie."""
    surface = FakeSurface(has_viewer=True, visibility_state="hidden")
    verdict = read_surface_presence(surface, user_initiated=True)
    assert verdict.present is False
    assert surface._state["hasFocus"] is True  # the lie is present in the signal


def test_read_surface_present_when_visible_and_watched() -> None:
    surface = FakeSurface(has_viewer=True, visibility_state="visible")
    assert read_surface_presence(surface, user_initiated=True).present is True


def test_read_surface_visibility_failure_fails_closed() -> None:
    class Boom:
        has_viewer = True

        def visibility(self) -> Future:
            future: Future = Future()
            future.set_exception(RuntimeError("surface gone"))
            return future

    assert read_surface_presence(Boom(), user_initiated=True).present is False


# --- the gate wired into the send path -------------------------------------


@pytest.fixture
def _restore_presence() -> Iterator[None]:
    original = ops.PRESENCE_SURFACE
    try:
        yield
    finally:
        ops.PRESENCE_SURFACE = original


def _seeded_contact(wired: Wired) -> str:  # noqa: F811
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired)
    with wired.db.repos() as repos:
        sarah = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/sarah-tan"))
        return sarah.id


def test_send_refuses_when_hidden_tab(
    wired: Wired, _restore_presence: None  # noqa: F811
) -> None:
    """The hidden-tab case, end to end through the send path: a fixture surface
    reports hidden, and the send REFUSES before touching the wire."""
    cid = _seeded_contact(wired)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver()
    ops.PRESENCE_SURFACE = lambda: FakeSurface(has_viewer=True, visibility_state="hidden")
    with pytest.raises(PresenceAbsent):
        ops.send_entrypoint(
            _ctx(wired.db, "send", {"contact_id": cid, "user_initiated": True})
        )


def test_send_refuses_when_no_viewer(
    wired: Wired, _restore_presence: None  # noqa: F811
) -> None:
    cid = _seeded_contact(wired)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver()
    ops.PRESENCE_SURFACE = lambda: FakeSurface(has_viewer=False, visibility_state="visible")
    with pytest.raises(PresenceAbsent):
        ops.send_entrypoint(
            _ctx(wired.db, "send", {"contact_id": cid, "user_initiated": True})
        )


def test_send_refuses_when_not_user_initiated(
    wired: Wired, _restore_presence: None  # noqa: F811
) -> None:
    cid = _seeded_contact(wired)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver()
    ops.PRESENCE_SURFACE = lambda: FakeSurface(has_viewer=True, visibility_state="visible")
    with pytest.raises(PresenceAbsent):
        # No user_initiated marker on the snapshot.
        ops.send_entrypoint(_ctx(wired.db, "send", {"contact_id": cid}))


def test_send_allows_when_present(
    wired: Wired, _restore_presence: None  # noqa: F811
) -> None:
    """All three hold → the send goes through and writes its OutreachLog."""
    cid = _seeded_contact(wired)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(
        connection_result={"op": "send-connection", "ok": True, "sent": True, "status": "sent"},
    )
    ops.PRESENCE_SURFACE = lambda: FakeSurface(has_viewer=True, visibility_state="visible")
    out = ops.send_entrypoint(
        _ctx(wired.db, "send", {
            "contact_id": cid, "message": "Hi Sarah!", "user_initiated": True,
        })
    )
    assert out.result_ref is not None and out.result_ref["sent"] is True
    with wired.db.repos() as repos:
        assert len(repos.outreach_logs.list_for_contact(cid)) == 1


def test_send_allows_when_no_streamed_surface_configured(
    wired: Wired, _restore_presence: None  # noqa: F811
) -> None:
    """No streamed surface live (`live_surface` returns None) → the gate steps
    aside for the self-launch/headed path, exactly as an unset seam does."""
    cid = _seeded_contact(wired)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(
        connection_result={"op": "send-connection", "ok": True, "sent": True, "status": "sent"},
    )
    ops.PRESENCE_SURFACE = lambda: None  # a broker peek with no live surface
    out = ops.send_entrypoint(
        _ctx(wired.db, "send", {
            "contact_id": cid, "message": "Hi Sarah!", "user_initiated": True,
        })
    )
    assert out.result_ref is not None and out.result_ref["sent"] is True


def test_discover_refuses_when_hidden_tab(
    wired: Wired, _restore_presence: None  # noqa: F811
) -> None:
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    ops.PRESENCE_SURFACE = lambda: FakeSurface(has_viewer=True, visibility_state="hidden")
    with pytest.raises(PresenceAbsent):
        ops.discover_entrypoint(
            _ctx(wired.db, "discover", {"company": "Northline", "user_initiated": True})
        )


def test_build_presence_surface_peeks_without_launching() -> None:
    """The host build helper is a peek: with no surface live for the slug, it
    hands back None (never launching Chrome), so the gate steps aside."""

    class FakeBroker:
        def __init__(self) -> None:
            self.peeked: list[str] = []

        def live_surface(self, slug: str) -> Any:
            self.peeked.append(slug)
            return None

    broker = FakeBroker()
    provider = ops.build_presence_surface(broker)
    assert provider() is None
    assert len(broker.peeked) == 1  # a peek happened; nothing launched
