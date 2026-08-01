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


# --- linkedin_search one-shot (discovery-expansion #6) ---------------------


def _connect(app, client) -> None:  # noqa: ANN001
    _enable_networking(client)
    op_id = client.post("/api/linkedin/connect", headers=AUTH, json={}).json()["id"]
    wait_for_state(app.state.db, op_id, "succeeded")


def _search_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    """A client whose fake driver ALSO returns two search-jobs rows."""
    yield from _make_client(
        tmp_path,
        lambda tier: FakeVoyagerDriver(
            login_result={
                "op": "login", "ok": True, "connected": True,
                "connected_as": "Ada Lovelace", "li_at_expires": None, "cookie_count": 4,
            },
            search_jobs_result={
                "op": "search-jobs", "ok": True, "count": 2, "total": 2,
                "jobs": [
                    {"id": "111", "url": "https://www.linkedin.com/jobs/view/111",
                     "title": "Backend Engineer", "company": "Acme",
                     "location": "Bengaluru, India (Remote)"},
                    {"id": "222", "url": "https://www.linkedin.com/jobs/view/222",
                     "title": "Platform Engineer", "company": "Beta",
                     "location": "Remote, India"},
                ],
            },
        ),
    )


@pytest.fixture
def search_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _search_client(tmp_path)


def _set_prefs(client: TestClient) -> None:
    resp = client.post("/api/settings", headers=AUTH, json={
        "role_aliases": ["backend engineer"], "locations": ["India"],
    })
    assert resp.status_code == 200


def _enable_job_search(client: TestClient, *, ack: bool = True) -> None:
    """The job search has its OWN opt-in + typed ack, stored in `ui_state` —
    a separate consent from the Referral Outreach toggle (posture doc §4 #8)."""
    ui: dict = {"linkedin_search_enabled": True}
    if ack:
        ui["linkedin_search_ack_at"] = "2026-08-01T00:00:00Z"
    resp = client.post("/api/settings", headers=AUTH, json={"ui_state": ui})
    assert resp.status_code == 200


def test_linkedin_search_requires_its_own_opt_in(search_client) -> None:
    """Referral Outreach being on is NOT consent for job search: the route used
    to check the wrong toggle, so enabling referrals alone unlocked searches the
    user never acknowledged."""
    _app, client = search_client
    resp = client.post("/api/linkedin/search", headers=AUTH)
    assert resp.status_code == 403

    _enable_networking(client)  # the OTHER feature's consent — still refused
    resp = client.post("/api/linkedin/search", headers=AUTH)
    assert resp.status_code == 403
    assert "job search is disabled" in resp.json()["detail"]


def test_linkedin_search_requires_the_typed_ack(search_client) -> None:
    """The ack checkbox was React `useState` only — never a server precondition."""
    _app, client = search_client
    _enable_job_search(client, ack=False)
    resp = client.post("/api/linkedin/search", headers=AUTH)
    assert resp.status_code == 403
    assert "acknowledgement" in resp.json()["detail"]


def test_linkedin_search_requires_connected_session(search_client) -> None:
    _app, client = search_client
    _enable_job_search(client)  # opted in but never connected
    resp = client.post("/api/linkedin/search", headers=AUTH)
    assert resp.status_code == 409
    assert "not connected" in resp.json()["detail"]


def test_linkedin_search_cannot_be_enqueued_generically(search_client) -> None:
    _app, client = search_client
    resp = client.post("/api/operations/linkedin_search", headers=AUTH, json={})
    assert resp.status_code == 422
    assert "search" in resp.json()["detail"]


def test_linkedin_search_persists_into_the_feed(search_client) -> None:
    _app, client = search_client
    _connect(_app, client)
    _enable_job_search(client)
    _set_prefs(client)

    resp = client.post("/api/linkedin/search", headers=AUTH)
    assert resp.status_code == 202
    assert resp.json()["kind"] == "linkedin_search"
    wait_for_state(_app.state.db, resp.json()["id"], "succeeded")

    # Both jobs landed in the feed, tagged source_adapter="linkedin", same funnel.
    jobs = client.get("/api/jobs", headers=AUTH).json()
    urls = {j["canonical_url"] for j in jobs}
    assert "https://www.linkedin.com/jobs/view/111" in urls
    assert "https://www.linkedin.com/jobs/view/222" in urls
    assert all(
        j["source_adapter"] == "linkedin"
        for j in jobs
        if j["canonical_url"].endswith(("/111", "/222"))
    )


def test_linkedin_search_limit_is_fixed_at_one_page(search_client) -> None:
    _app, client = search_client
    _connect(_app, client)
    _enable_job_search(client)
    _set_prefs(client)

    # EVERY request runs one page of 25, whatever the caller asks for. The wire
    # request carries `count=25` regardless (worker pages at _PAGE=25), so a
    # smaller limit never made a smaller request — it only discarded rows we
    # had already received while looking like a lighter footprint.
    for asked in (10, 1, 9999):
        r = client.post("/api/linkedin/search", headers=AUTH, json={"limit": asked})
        assert r.status_code == 202
        wait_for_state(_app.state.db, r.json()["id"], "succeeded")
        with _app.state.db.repos() as repos:
            assert repos.operations.get(r.json()["id"]).input_snapshot["limit"] == 25

    # Omitted → the same single page.
    d = client.post("/api/linkedin/search", headers=AUTH, json={})
    wait_for_state(_app.state.db, d.json()["id"], "succeeded")
    with _app.state.db.repos() as repos:
        assert repos.operations.get(d.json()["id"]).input_snapshot["limit"] == 25


def test_linkedin_search_dedups_against_existing_guest_row(search_client) -> None:
    _app, client = search_client
    _connect(_app, client)
    _enable_job_search(client)
    _set_prefs(client)
    # A job the guest adapter already stored at the same canonical URL.
    db = _app.state.db
    with db.repos() as repos:
        repos.jobs.create(
            canonical_url="https://www.linkedin.com/jobs/view/111",
            title="Backend Engineer", company="Acme", location="Remote",
            description="", source_adapter="linkedin",
        )
        repos.commit()
    resp = client.post("/api/linkedin/search", headers=AUTH)
    wait_for_state(_app.state.db, resp.json()["id"], "succeeded")
    op = client.get(f"/api/operations/{resp.json()['id']}", headers=AUTH).json()
    # 2 found, 1 deduped (the pre-existing guest row), 1 newly persisted.
    scan = op["result_ref"]["scan"]
    assert scan["deduped"] == 1
    assert scan["persisted"] == 1


# --- user-initiated contact sync (FR-NW-15) --------------------------------
# contact_sync used to be a 12 h schedule that touched LinkedIn with nobody
# present. It is now user-initiated only: an explicit Sync button (force=true)
# plus a throttled opportunistic refresh when the Networking surface opens.
# See `docs/internal/linkedin-posture.md` §1.


def test_contact_sync_is_not_a_seeded_schedule(app_client) -> None:
    _app, client = app_client
    kinds = {s["kind"] for s in client.get("/api/schedules", headers=AUTH).json()}
    assert "contact_sync" not in kinds


def test_contact_sync_cannot_be_enqueued_generically(app_client) -> None:
    """The generic enqueue route must not be a way around the consent gate and
    the throttle — it previously accepted `contact_sync` ungated."""
    _app, client = app_client
    resp = client.post("/api/operations/contact_sync", headers=AUTH, json={})
    assert resp.status_code == 422
    assert "/api/networking/contact-sync" in resp.json()["detail"]


def test_contact_sync_requires_the_master_toggle(app_client) -> None:
    _app, client = app_client
    resp = client.post("/api/networking/contact-sync", headers=AUTH)
    assert resp.status_code == 403


def test_contact_sync_requires_a_valid_session(app_client) -> None:
    _app, client = app_client
    _enable_networking(client)
    resp = client.post("/api/networking/contact-sync", headers=AUTH)
    assert resp.status_code == 409


def test_contact_sync_runs_when_enabled_and_connected(app_client) -> None:
    _app, client = app_client
    _connect(_app, client)
    resp = client.post("/api/networking/contact-sync", headers=AUTH)
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "queued"
    assert body["throttled"] is False
    assert body["id"]


def test_opportunistic_refresh_is_throttled_but_the_button_is_not(app_client) -> None:
    _app, client = app_client
    _connect(_app, client)
    first = client.post("/api/networking/contact-sync", headers=AUTH).json()
    wait_for_state(_app.state.db, first["id"], "succeeded")

    # Surface re-open (force absent → false): declined, with a next-eligible time.
    throttled = client.post("/api/networking/contact-sync", headers=AUTH).json()
    assert throttled["state"] == "throttled"
    assert throttled["throttled"] is True
    assert throttled["next_eligible_at"]
    assert throttled["id"] is None

    # The user pressing Sync overrides the throttle — they asked for it.
    forced = client.post(
        "/api/networking/contact-sync?force=true", headers=AUTH
    ).json()
    assert forced["state"] == "queued"
    assert forced["id"]


# --- caps + plan surface (posture doc §4 fixes 7 + 10) ----------------------


def test_quota_caps_come_from_the_package(app_client) -> None:
    """The popup's displayed caps must be the ENFORCED caps. `dto._TIER_CAPS`
    (15/100 · 30/200) drifted to 2-3× the package's real values and is gone —
    this pins the route to the package tier table, DM budgets included."""
    from sidecar.packages.referral_outreach import TIERS

    _app, client = app_client
    q = client.get("/api/referrals/quota", headers=AUTH).json()
    new = TIERS["new"]
    assert q["daily_limit"] == new.invites.day == 8
    assert q["weekly_limit"] == new.invites.week == 30
    assert q["dm_daily_limit"] == new.dms.day == 10
    assert q["dm_weekly_limit"] == new.dms.week == 50


def test_linkedin_plan_is_settable_and_surfaced(app_client) -> None:
    _app, client = app_client
    s = client.get("/api/linkedin/session", headers=AUTH).json()
    assert s["linkedin_plan"] == "free"  # conservative default

    updated = client.post(
        "/api/linkedin/tier", headers=AUTH, json={"linkedin_plan": "premium"}
    )
    assert updated.status_code == 200
    assert updated.json()["linkedin_plan"] == "premium"
    # Tier untouched by a plan-only update.
    assert updated.json()["account_tier"] == "new"


def test_tier_route_rejects_bad_values(app_client) -> None:
    _app, client = app_client
    # `recovering` is reserved for the restriction ladder — not user-selectable.
    r = client.post("/api/linkedin/tier", headers=AUTH, json={"account_tier": "recovering"})
    assert r.status_code == 422
    r = client.post("/api/linkedin/tier", headers=AUTH, json={"linkedin_plan": "gold"})
    assert r.status_code == 422
    r = client.post("/api/linkedin/tier", headers=AUTH, json={})
    assert r.status_code == 422


def test_reach_out_contact_list_is_capped(app_client) -> None:
    """An unbounded contact list meant one request could authorise arbitrarily
    many real sends (posture doc §5.1). The UI sends one per confirm; the DTO
    hard-caps at 10."""
    _app, client = app_client
    contacts = [{"contact_id": f"c{i}", "message": "hi"} for i in range(11)]
    r = client.post(
        "/api/referrals/reach-out", headers=AUTH,
        json={"contacts": contacts},
    )
    assert r.status_code == 422


# --- gate holes (posture doc §4 #8, closed 2026-08-01) ---------------------
# connect/resume ran with NO gate at all: anything reaching the sidecar could
# open a real browser at linkedin.com, or clear the 24 h rate-limit backoff,
# with both LinkedIn features switched off.


def test_connect_refused_when_no_linkedin_feature_is_enabled(app_client) -> None:
    _app, client = app_client
    resp = client.post("/api/linkedin/connect", headers=AUTH, json={})
    assert resp.status_code == 403
    assert "No LinkedIn feature is enabled" in resp.json()["detail"]


def test_connect_allowed_by_either_feature(app_client) -> None:
    """The session is shared: job-search-only users must be able to connect."""
    _app, client = app_client
    _enable_job_search(client)  # job search only, referrals still off
    resp = client.post("/api/linkedin/connect", headers=AUTH, json={})
    assert resp.status_code == 202


def test_resume_refused_when_no_linkedin_feature_is_enabled(app_client) -> None:
    """Resume is the one route that switches a SAFETY mechanism off — it clears
    the backoff LinkedIn's own throttle signal put us in."""
    _app, client = app_client
    resp = client.post("/api/linkedin/resume", headers=AUTH)
    assert resp.status_code == 403


def test_login_op_self_gates_even_if_enqueued(app_client) -> None:
    """Defence in depth: the op re-checks its own precondition rather than
    trusting whoever enqueued it — it launches a real browser."""
    from sidecar.app.registry.linkedin_op import login_entrypoint
    from sidecar.app.registry.operations import OperationContext

    _app, _client = app_client
    ctx = OperationContext(
        kind="linkedin_login", input_snapshot={}, db=_app.state.db, operation_id="op-test"
    )
    outcome = login_entrypoint(ctx)
    assert outcome.result_ref["skipped"] == "no_linkedin_feature_enabled"
    assert outcome.result_ref["connected"] is False
