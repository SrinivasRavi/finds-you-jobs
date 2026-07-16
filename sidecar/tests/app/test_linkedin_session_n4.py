"""Covers: Track N4 — LinkedIn session capture + lifecycle (through the real app).

- US-SET-06 — connect (headed login op) / disconnect / validate / status DTO
- FR-NW-05 / US-REF-09 — backoff surfacing on a rate-limited send + manual resume
- US-NW-11 / FR-NW-13 — auto-archive never-accepted connections after 60 days
- US-NW-10 / US-REF-08 — account-tier selection passed to voyager

ZERO live LinkedIn traffic: the voyager driver factory is monkeypatched to the
in-memory `FakeVoyagerDriver` (no subprocess, no browser, no network). The real
headed-login *plumbing* is verified separately against a LOCAL fixture in
`voyager_py/tests/test_login_capture.py`.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.db.base import now_utc
from sidecar.app.main import create_app
from sidecar.app.registry import networker_ops as ops

from ..modules.networker.fakes import FakeVoyagerDriver
from .conftest import wait_for_state

TOKEN = "test-token-n4"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _make_client(
    tmp_path: Path, driver_factory
) -> Generator[tuple[FastAPI, TestClient]]:
    original = ops.DRIVER_FACTORY
    ops.DRIVER_FACTORY = driver_factory
    app = create_app(token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
                     enable_scheduler=False)
    try:
        with TestClient(app) as client:
            yield app, client
    finally:
        ops.DRIVER_FACTORY = original


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _make_client(
        tmp_path,
        lambda tier: FakeVoyagerDriver(login_result={
            "op": "login", "ok": True, "connected": True,
            "connected_as": "Ada Lovelace", "li_at_expires": None, "cookie_count": 4,
        }),
    )


def _enable_networking(client: TestClient) -> None:
    resp = client.post("/api/settings", headers=AUTH, json={"voyager_risk_marker_on": True})
    assert resp.status_code == 200


# --- connect (headed login op) --------------------------------------------


def test_connect_captures_session_and_flips_status(app_client) -> None:
    _app, client = app_client
    _enable_networking(client)

    # Before connect: never_set.
    before = client.get("/api/linkedin/session", headers=AUTH).json()
    assert before["status"] == "never_set" and before["enabled"] is True

    resp = client.post("/api/linkedin/connect", headers=AUTH, json={})
    assert resp.status_code == 202
    op_id = resp.json()["id"]
    assert resp.json()["kind"] == "linkedin_login"
    wait_for_state(_app.state.db, op_id, "succeeded")

    after = client.get("/api/linkedin/session", headers=AUTH).json()
    assert after["status"] == "valid"
    assert after["connected_as"] == "Ada Lovelace"
    assert after["last_validated_at"] is not None


def test_connect_cannot_be_enqueued_generically(app_client) -> None:
    _app, client = app_client
    resp = client.post("/api/operations/linkedin_login", headers=AUTH, json={})
    assert resp.status_code == 422
    assert "connect" in resp.json()["detail"]


def test_disconnect_clears_session(app_client) -> None:
    _app, client = app_client
    _enable_networking(client)
    op_id = client.post("/api/linkedin/connect", headers=AUTH, json={}).json()["id"]
    wait_for_state(_app.state.db, op_id, "succeeded")

    resp = client.post("/api/linkedin/disconnect", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "never_set" and body["connected_as"] == ""


def test_validate_local_only_marks_valid(tmp_path) -> None:
    # session_status returns valid → validate flips + stamps last_validated_at.
    it = _make_client(
        tmp_path,
        lambda tier: FakeVoyagerDriver(session_status_result={
            "op": "session-status", "ok": True, "status": "valid",
            "present": True, "has_auth_cookie": True, "expired": False,
        }),
    )
    _app, client = next(it)
    try:
        _enable_networking(client)
        resp = client.post("/api/linkedin/validate", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "valid"
        assert resp.json()["last_validated_at"] is not None
    finally:
        it.close()


def test_validate_expired_session(tmp_path) -> None:
    it = _make_client(
        tmp_path,
        lambda tier: FakeVoyagerDriver(session_status_result={
            "op": "session-status", "ok": True, "status": "expired",
            "present": True, "has_auth_cookie": True, "expired": True,
        }),
    )
    _app, client = next(it)
    try:
        _enable_networking(client)
        resp = client.post("/api/linkedin/validate", headers=AUTH)
        assert resp.json()["status"] == "expired"
    finally:
        it.close()


def test_set_tier(app_client) -> None:
    _app, client = app_client
    resp = client.post("/api/linkedin/tier", headers=AUTH, json={"account_tier": "seasoned"})
    assert resp.status_code == 200 and resp.json()["account_tier"] == "seasoned"
    bad = client.post("/api/linkedin/tier", headers=AUTH, json={"account_tier": "wild"})
    assert bad.status_code == 422


# --- backoff surfacing + manual resume (FR-NW-05 / US-REF-09) --------------


def test_rate_limited_send_flips_to_backing_off_then_resume(tmp_path) -> None:
    paused_until = now_utc().timestamp() + 86400
    it = _make_client(
        tmp_path,
        lambda tier: FakeVoyagerDriver(
            connection_result={
                "op": "send-connection", "ok": False, "sent": False,
                "error": "rate_limited", "reason": "You've reached the weekly invitation limit",
                "quota": {"paused": True, "paused_until": paused_until},
            },
            session_status_result={
                "op": "session-status", "ok": True, "status": "valid",
                "present": True, "has_auth_cookie": True, "expired": False,
            },
            resume_result={"op": "resume", "ok": True,
                           "quota": {"paused": False, "paused_until": 0.0}},
        ),
    )
    _app, client = next(it)
    try:
        _enable_networking(client)
        # A contact + job so reach-out has a target.
        contact = client.post("/api/contacts", headers=AUTH, json={
            "linkedin_url": "https://www.linkedin.com/in/x", "name": "X Y",
            "current_company": "Acme", "connection_status": "sent",
        }).json()
        job = client.post("/api/jobs", headers=AUTH, json={
            "canonical_url": "https://ex.co/j", "title": "Eng", "company": "Acme",
            "location": "Remote", "description": "desc",
        }).json()
        resp = client.post("/api/referrals/reach-out", headers=AUTH, json={
            "job_id": job["id"], "application_id": None,
            "contacts": [{"contact_id": contact["id"], "message": "hi"}],
        })
        assert resp.status_code == 202
        op_id = resp.json()["enqueued"][0]
        wait_for_state(_app.state.db, op_id, "succeeded")

        session = client.get("/api/linkedin/session", headers=AUTH).json()
        assert session["status"] == "backing_off"
        assert "weekly invitation limit" in session["paused_reason"]
        assert session["paused_until"] is not None

        # Manual resume clears the pause.
        resumed = client.post("/api/linkedin/resume", headers=AUTH)
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "valid"
        assert resumed.json()["paused_until"] is None
    finally:
        it.close()


# --- auto-archive (US-NW-11 / FR-NW-13) ------------------------------------


def test_archive_stale_contacts_op(app_client) -> None:
    _app, client = app_client
    _enable_networking(client)

    # A stale sent-but-never-accepted contact (sent 61 days ago) + a fresh one.
    stale = client.post("/api/contacts", headers=AUTH, json={
        "linkedin_url": "https://www.linkedin.com/in/stale", "name": "Stale One",
        "current_company": "Acme", "connection_status": "sent",
    }).json()
    fresh = client.post("/api/contacts", headers=AUTH, json={
        "linkedin_url": "https://www.linkedin.com/in/fresh", "name": "Fresh One",
        "current_company": "Acme", "connection_status": "sent",
    }).json()
    accepted = client.post("/api/contacts", headers=AUTH, json={
        "linkedin_url": "https://www.linkedin.com/in/acc", "name": "Acc One",
        "current_company": "Acme", "connection_status": "accepted",
    }).json()

    # Backdate the stale contact's sent_at directly via the DB.
    db = _app.state.db
    old = now_utc() - timedelta(days=61)
    with db.repos() as repos:
        repos.contacts.update(stale["id"], sent_at=old)
        repos.contacts.update(fresh["id"], sent_at=now_utc())
        repos.contacts.update(accepted["id"], sent_at=old, accepted_at=old)
        repos.commit()

    resp = client.post("/api/operations/archive_stale_contacts", headers=AUTH, json={})
    wait_for_state(_app.state.db, resp.json()["id"], "succeeded")

    live = {c["id"] for c in client.get("/api/contacts", headers=AUTH).json()}
    assert stale["id"] not in live      # archived
    assert fresh["id"] in live          # too recent
    assert accepted["id"] in live       # ever accepted → never auto-archived
