# voyager_py/tests/test_send_settles_by_reading.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""A send that ends in doubt is settled by reading LinkedIn, never by guessing
(architecture-audit-2026-10-14.md section 4.2). Before, a timeout mid-DM was logged
Failed while the message went out, and Retry sent it twice."""

from __future__ import annotations

import time

import pytest

from sidecar.packages.referral_outreach.upstream import actions, pacing, session, worker
from sidecar.packages.referral_outreach.upstream.errors import RateLimited, SkipProfile
from sidecar.packages.referral_outreach.upstream.pacing import Pacer, resolve_profile


class _Session:
    def __init__(self, **_kwargs) -> None:
        self.runs = 0

    def run_browser(self, action, timeout_s=None):
        self.runs += 1
        return action()

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(session, "AccountSession", _Session)
    monkeypatch.setattr(pacing, "send_delay_seconds", lambda: 0.0)
    monkeypatch.setattr(worker, "SETTLE_WAITS_S", (0.0, 0.0))
    monkeypatch.setattr(worker, "PRECHECK_WAITS_S", (0.0,))


def _dms_used(state_dir) -> int:
    return Pacer(resolve_profile(None), state_dir=state_dir).usage("dms")["day_used"]


def _thread(monkeypatch, direction, at):
    reads: list[str] = []

    def state(_s, pid):
        reads.append(pid)
        return {"last_message_direction": direction, "last_message_at": at}

    monkeypatch.setattr(actions, "get_contact_sync_state", state)
    return reads


def _dies_after_compose(monkeypatch):
    def send(_s, _pid, _msg, *, on_step):
        on_step("dm2")
        on_step("dm3")
        raise TimeoutError("lane deadline")

    monkeypatch.setattr(actions, "send_dm", send)


def test_a_dm_that_died_mid_send_but_landed_reads_sent(tmp_path, monkeypatch):
    _dies_after_compose(monkeypatch)
    _thread(monkeypatch, "me", time.time())
    out = worker.send_dm("ana", "hello there", state_dir=str(tmp_path))
    assert out["sent"] is True and out["ok"] is True
    assert "TimeoutError" in out["confirmed_after"]
    assert _dms_used(tmp_path) == 1  # it went out, so it stays charged


def test_a_dm_that_died_and_did_not_land_reads_not_sent_and_refunds(tmp_path, monkeypatch):
    _dies_after_compose(monkeypatch)
    _thread(monkeypatch, "me", time.time() - 86_400)  # our last message is yesterday's
    out = worker.send_dm("ana", "hello there", state_dir=str(tmp_path))
    assert out["sent"] is False and out["error"] == "not_sent"
    assert "safe" in out["reason"]
    assert _dms_used(tmp_path) == 0


def test_a_dm_that_cannot_be_checked_is_unconfirmed_and_stays_charged(tmp_path, monkeypatch):
    _dies_after_compose(monkeypatch)

    def throttled(_s, _pid):
        raise RateLimited("HTTP 429")

    monkeypatch.setattr(actions, "get_contact_sync_state", throttled)
    out = worker.send_dm("ana", "hello there", state_dir=str(tmp_path))
    assert out["sent"] is False and out["error"] == "unconfirmed"
    assert _dms_used(tmp_path) == 1


def test_a_compose_ui_error_after_it_opened_is_checked_not_refunded(tmp_path, monkeypatch):
    """`_send_dm_via_ui` returns False on any Playwright error, after the click too.
    That used to read as a proven no-send and refund a DM that went out."""

    def send(_s, _pid, _msg, *, on_step):
        on_step("dm3")
        return False

    monkeypatch.setattr(actions, "send_dm", send)
    _thread(monkeypatch, "me", time.time())
    out = worker.send_dm("ana", "hello there", state_dir=str(tmp_path))
    assert out["sent"] is True


def test_a_failure_before_the_compose_opens_is_a_plain_failure(tmp_path, monkeypatch):
    def send(_s, _pid, _msg, *, on_step):
        raise TimeoutError("profile never loaded")

    monkeypatch.setattr(actions, "send_dm", send)
    reads = _thread(monkeypatch, "me", time.time())
    with pytest.raises(TimeoutError):
        worker.send_dm("ana", "hello there", state_dir=str(tmp_path))
    assert reads == []  # nothing to check: the message was never typed


def test_a_retry_of_an_unconfirmed_dm_that_landed_sends_nothing(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(actions, "send_dm", lambda *a, **k: sent.append("x") or True)
    attempt = time.time() - 60
    _thread(monkeypatch, "me", attempt + 5)
    out = worker.send_dm("ana", "hello", state_dir=str(tmp_path), unconfirmed_since=attempt)
    assert out["sent"] is True and sent == []


def test_a_retry_of_an_unconfirmed_dm_that_did_not_land_sends_once(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(actions, "send_dm", lambda *a, **k: sent.append("x") or True)
    _thread(monkeypatch, "them", time.time() - 86_400)
    out = worker.send_dm(
        "ana", "hello", state_dir=str(tmp_path), unconfirmed_since=time.time() - 60
    )
    assert out["sent"] is True and sent == ["x"]


def test_a_retry_that_still_cannot_read_linkedin_sends_nothing(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(actions, "send_dm", lambda *a, **k: sent.append("x") or True)

    def broken(_s, _pid):
        raise OSError("browser would not launch")

    monkeypatch.setattr(actions, "get_contact_sync_state", broken)
    out = worker.send_dm(
        "ana", "hello", state_dir=str(tmp_path), unconfirmed_since=time.time() - 60
    )
    assert out["error"] == "unconfirmed" and sent == []


def test_an_invite_unconfirmed_after_the_click_reads_pending_as_sent(tmp_path, monkeypatch):
    def send(_s, _pid, note="", *, on_step):
        on_step("invite2")
        on_step("invite3")
        raise SkipProfile("connection request could not be confirmed sent")

    monkeypatch.setattr(actions, "send_connection_request", send)
    monkeypatch.setattr(actions, "get_connection_status", lambda _s, _p: actions.STATUS_PENDING)
    out = worker.send_connection("bo", note="hi", state_dir=str(tmp_path))
    assert out["sent"] is True


def test_an_invite_that_did_not_go_out_refunds_invite_and_note(tmp_path, monkeypatch):
    def send(_s, _pid, note="", *, on_step):
        on_step("invite3")
        raise TimeoutError("lane deadline")

    monkeypatch.setattr(actions, "send_connection_request", send)
    monkeypatch.setattr(
        actions, "get_connection_status", lambda _s, _p: actions.STATUS_QUALIFIED
    )
    out = worker.send_connection("bo", note="hi", state_dir=str(tmp_path))
    assert out["error"] == "not_sent"
    pacer = Pacer(resolve_profile(None), state_dir=tmp_path)
    assert pacer.remaining()["daily_used"] == 0
    assert pacer.usage("notes")["month_used"] == 0
