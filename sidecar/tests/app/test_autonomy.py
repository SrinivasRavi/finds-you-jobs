"""Covers: autonomy settings (audit-2026-10-14.md section 8.10, P9).

A click (`requested_by="user"`) always runs. A background caller (`"app"`, the
scheduler or the autopilot) runs a send, a LinkedIn read or a Submit only if the
user switched that action to autopilot. The caller comes from the op row, never
from the snapshot. Fake drivers: 0 LinkedIn, 0 browser."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sidecar.app.registry import autonomy
from sidecar.app.registry import networker_ops as ops
from sidecar.app.registry.autonomy import NotAuthorized, refusal
from sidecar.app.registry.operations import default_operation_registry

from ..modules.networker.fakes import FakeVoyagerDriver
from ._support import AUTH
from .conftest import migrated_db  # noqa: F401 — fixture
from .test_networker_ops_n3 import (  # noqa: F401 — reuse the seeded-DB fixtures
    DISCOVER_ROWS,
    Wired,
    _ctx,
    _nn,
    _seed,
    wired,
)


def _prefs(**on: bool) -> SimpleNamespace:
    base = {"autopilot_invite": False, "autopilot_dm": False, "autopilot_apply": False}
    return SimpleNamespace(**{**base, **on})


# --- the pure decision -----------------------------------------------------


@pytest.mark.parametrize("action", ["invite", "dm", "apply", "linkedin_read", "view"])
def test_a_click_always_runs(action: str) -> None:
    assert refusal(_prefs(), action, "user") is None


@pytest.mark.parametrize("action", ["invite", "dm", "apply", "linkedin_read"])
def test_a_background_caller_is_refused_while_the_action_is_confirm(action: str) -> None:
    reason = refusal(_prefs(), action, "app")
    assert reason is not None and "autopilot is off" in reason


def test_each_switch_opens_only_its_own_action() -> None:
    assert refusal(_prefs(autopilot_dm=True), "dm", "app") is None
    assert refusal(_prefs(autopilot_dm=True), "invite", "app") is not None
    assert refusal(_prefs(autopilot_invite=True), "invite", "app") is None
    assert refusal(_prefs(autopilot_invite=True), "apply", "app") is not None
    assert refusal(_prefs(autopilot_apply=True), "apply", "app") is None


def test_a_linkedin_read_runs_if_either_outreach_switch_is_on() -> None:
    assert refusal(_prefs(autopilot_invite=True), "linkedin_read", "app") is None
    assert refusal(_prefs(autopilot_dm=True), "linkedin_read", "app") is None


def test_the_live_view_is_never_a_background_action() -> None:
    on = _prefs(autopilot_invite=True, autopilot_dm=True, autopilot_apply=True)
    assert refusal(on, "view", "app") is not None


def test_an_unknown_caller_is_refused() -> None:
    on = _prefs(autopilot_invite=True, autopilot_dm=True, autopilot_apply=True)
    assert refusal(on, "dm", "") is not None


# --- prevention: every op kind is classified -------------------------------


def test_every_op_kind_is_classified() -> None:
    """A new kind fails here until it is put in GATED (it sends, submits or
    reads logged-in LinkedIn) or UNGATED (it does none of those)."""
    kinds = default_operation_registry().kinds()
    gated, ungated = set(autonomy.GATED_KINDS), set(autonomy.UNGATED_KINDS)
    assert not gated & ungated
    assert kinds == gated | ungated


# --- the gate wired into the op paths --------------------------------------


def _seeded(wired: Wired, url: str) -> str:  # noqa: F811
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired)
    with wired.db.repos() as repos:
        return _nn(repos.contacts.get_by_url(url)).id


def _set(wired: Wired, **fields: bool) -> None:  # noqa: F811
    with wired.db.repos() as repos:
        repos.preferences.update(**fields)


_SARAH = "https://www.linkedin.com/in/sarah-tan"  # 2nd degree: an invite
_RAJ = "https://www.linkedin.com/in/raj-io"  # 1st degree: a DM
_SENT = {"op": "send-connection", "ok": True, "sent": True, "status": "sent"}
_DM_SENT = {"op": "send-dm", "ok": True, "sent": True, "status": "sent"}


def test_send_refuses_a_background_caller_before_the_wire(wired: Wired) -> None:  # noqa: F811
    cid = _seeded(wired, _SARAH)
    built: list[object] = []
    ops.DRIVER_FACTORY = lambda _profile: built.append(1) or FakeVoyagerDriver()
    with pytest.raises(NotAuthorized, match="invite"):
        ops.send_entrypoint(
            _ctx(wired.db, "send", {"contact_id": cid, "message": "Hi"}, requested_by="app")
        )
    assert built == []
    with wired.db.repos() as repos:
        assert repos.outreach_logs.list_for_contact(cid) == []


def test_send_ignores_a_user_initiated_flag_in_the_snapshot(wired: Wired) -> None:  # noqa: F811
    """A retry copies the snapshot, so a flag there proves nothing."""
    cid = _seeded(wired, _SARAH)
    with pytest.raises(NotAuthorized):
        ops.send_entrypoint(_ctx(
            wired.db, "send", {"contact_id": cid, "message": "Hi", "user_initiated": True},
            requested_by="app",
        ))


def test_invite_autopilot_sends_an_invite_and_not_a_dm(wired: Wired) -> None:  # noqa: F811
    sarah = _seeded(wired, _SARAH)
    with wired.db.repos() as repos:
        raj = _nn(repos.contacts.get_by_url(_RAJ)).id
    _set(wired, autopilot_invite=True)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(connection_result=_SENT)
    out = ops.send_entrypoint(
        _ctx(wired.db, "send", {"contact_id": sarah, "message": "Hi"}, requested_by="app")
    )
    assert _nn(out.result_ref)["sent"] is True
    with pytest.raises(NotAuthorized, match="message"):
        ops.send_entrypoint(
            _ctx(wired.db, "send", {"contact_id": raj, "message": "Hi"}, requested_by="app")
        )


def test_dm_autopilot_sends_to_a_connection(wired: Wired) -> None:  # noqa: F811
    raj = _seeded(wired, _RAJ)
    _set(wired, autopilot_dm=True)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(dm_result=_DM_SENT)
    out = ops.send_entrypoint(
        _ctx(wired.db, "send", {"contact_id": raj, "message": "Hi"}, requested_by="app")
    )
    assert _nn(out.result_ref)["sent"] is True


def test_discover_refuses_a_background_caller(wired: Wired) -> None:  # noqa: F811
    drv = FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda _profile: drv
    with pytest.raises(NotAuthorized):
        ops.discover_entrypoint(
            _ctx(wired.db, "discover", {"company": "Northline"}, requested_by="app")
        )
    _set(wired, autopilot_invite=True)
    out = ops.discover_entrypoint(
        _ctx(wired.db, "discover", {"company": "Northline"}, requested_by="app")
    )
    assert _nn(out.result_ref)["count"] == 3


def test_a_click_still_sends_with_every_switch_off(wired: Wired) -> None:  # noqa: F811
    cid = _seeded(wired, _SARAH)
    ops.DRIVER_FACTORY = lambda _profile: FakeVoyagerDriver(connection_result=_SENT)
    out = ops.send_entrypoint(_ctx(wired.db, "send", {"contact_id": cid, "message": "Hi"}))
    assert _nn(out.result_ref)["sent"] is True


def test_a_background_apply_is_refused_and_its_run_lands_failed(migrated_db) -> None:  # noqa: F811
    from sidecar.app.registry import apply_op
    from sidecar.app.registry.operations import OperationContext

    from .test_apply_op import _pending_packet_card

    app_id, run_id = _pending_packet_card(migrated_db)
    ctx = OperationContext(
        kind="apply", input_snapshot={"run_id": run_id, "application_id": app_id},
        db=migrated_db, operation_id="op-p9", requested_by="app",
    )
    with pytest.raises(NotAuthorized, match="Submit"):
        apply_op._apply(ctx)
    with migrated_db.repos() as repos:
        run = _nn(repos.apply_runs.get(run_id))
        assert run.status == "failed"
        assert "autopilot is off" in run.summary


# --- the settings route ----------------------------------------------------


def test_settings_start_on_confirm_and_save_each_switch(app_client) -> None:
    _app, client = app_client
    prefs = client.get("/api/settings", headers=AUTH).json()["preferences"]
    assert (prefs["autopilot_invite"], prefs["autopilot_dm"], prefs["autopilot_apply"]) == (
        False, False, False,
    )
    assert prefs["autopilot_min_score"] == 80
    resp = client.post("/api/settings", headers=AUTH, json={
        "autopilot_apply": True, "autopilot_min_score": 70, "autopilot_applies_per_day": 5,
    })
    assert resp.status_code == 200
    saved = resp.json()["preferences"]
    assert saved["autopilot_apply"] is True and saved["autopilot_invite"] is False
    assert (saved["autopilot_min_score"], saved["autopilot_applies_per_day"]) == (70, 5)


@pytest.mark.parametrize("field,value", [
    ("autopilot_min_score", 101), ("autopilot_contacts_per_job", 0),
    ("autopilot_applies_per_day", 101),
])
def test_settings_refuse_a_limit_out_of_range(app_client, field: str, value: int) -> None:
    _app, client = app_client
    assert client.post("/api/settings", headers=AUTH, json={field: value}).status_code == 422
