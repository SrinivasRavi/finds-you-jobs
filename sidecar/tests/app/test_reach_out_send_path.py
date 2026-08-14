"""Phase 6: the reach-out → send path over the broker-backed driver, at the route.

Proves the route-level guarantees of a referral send batch, with ZERO account use
(FakeVoyagerDriver via the `DRIVER_FACTORY` seam — no browser, no LinkedIn):

  - the per-action confirm gate (US-NW-09): reach-out enqueues exactly ONE
    discrete `send` op per selected contact, each carrying its own message and
    all tied by one batch id — the UI confirms one contact per send;
  - the single-flight idempotency guard skips a contact whose send for this role
    is already queued/running, and a duplicate contact inside one request;
  - the server-side networking gate refuses the whole route when Referral
    Outreach is off (audit P2-4), independent of the UI;
  - the popup's quota snapshot reads the SAME package ledger the send path
    enforces — the counter can never show head-room a send would refuse.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.registry import networker_ops as ops

from ..modules.networker.fakes import FakeVoyagerDriver

TOKEN = "test-token-p6"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _make_client(tmp_path: Path) -> Generator[tuple[FastAPI, TestClient]]:
    """A real app with the voyager driver factory pinned to the in-memory fake
    (default: a landing send). The seam is restored on teardown."""
    original = ops.DRIVER_FACTORY
    ops.DRIVER_FACTORY = lambda profile: FakeVoyagerDriver(
        connection_result={"op": "send-connection", "ok": True, "sent": True, "status": "sent"},
        dm_result={"op": "send-dm", "ok": True, "sent": True, "status": "sent"},
    )
    app = create_app(token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
                     enable_scheduler=False)
    try:
        with TestClient(app) as client:
            yield app, client
    finally:
        ops.DRIVER_FACTORY = original


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _make_client(tmp_path)


def _enable_networking(client: TestClient) -> None:
    resp = client.post("/api/settings", headers=AUTH, json={"voyager_risk_marker_on": True})
    assert resp.status_code == 200


def _make_job(client: TestClient, slug: str = "north") -> str:
    return client.post("/api/jobs", headers=AUTH, json={
        "canonical_url": f"https://ex.co/j/{slug}", "title": "Backend Engineer",
        "company": "Northline", "location": "Remote", "description": "desc",
    }).json()["id"]


def _make_contact(client: TestClient, slug: str) -> str:
    return client.post("/api/contacts", headers=AUTH, json={
        "linkedin_url": f"https://www.linkedin.com/in/{slug}", "name": f"Name {slug}",
        "current_company": "Northline", "connection_status": "candidate",
    }).json()["id"]


def _skipped(body: dict) -> list[str]:
    """Read the skipped list under either the serialization alias or field name."""
    return body.get("skippedContactIds", body.get("skipped_contact_ids", []))


# ── one discrete, individually-gated send op per confirmed contact ──────────


def test_reach_out_enqueues_one_gated_send_per_contact(app_client) -> None:
    """The UI confirms one contact per send; the route fans that out to exactly
    one discrete `send` op per selected contact, each with its own message, all
    tied by ONE batch id (US-NW-09 / FR-NW-03)."""
    app, client = app_client
    _enable_networking(client)
    job = _make_job(client)
    cids = [_make_contact(client, s) for s in ("a", "b", "c")]

    resp = client.post("/api/referrals/reach-out", headers=AUTH, json={
        "job_id": job, "application_id": None,
        "contacts": [{"contact_id": c, "message": f"hi {c}"} for c in cids],
    })
    assert resp.status_code == 202
    body = resp.json()
    enqueued = body["enqueued"]
    assert len(enqueued) == 3
    assert _skipped(body) == []

    with app.state.db.repos() as repos:
        rows = [repos.operations.get(op_id) for op_id in enqueued]
    assert all(r is not None and r.kind == "send" for r in rows)
    # One discrete op per contact, each carrying its own message…
    assert {r.input_snapshot["contact_id"] for r in rows} == set(cids)
    assert {r.input_snapshot["message"] for r in rows} == {f"hi {c}" for c in cids}
    # …all tied by ONE batch id (the batch-settle key, FR-NW-03).
    assert len({r.input_snapshot["batch_id"] for r in rows}) == 1


def test_reach_out_skips_a_contact_already_in_flight(app_client) -> None:
    """The single-flight guard: a contact whose send for this role is already
    queued/running is skipped, never enqueued a second time — a repeated
    "Send now" cannot fire a duplicate real invite (US-NW-09)."""
    app, client = app_client
    _enable_networking(client)
    job = _make_job(client)
    a, b = _make_contact(client, "a"), _make_contact(client, "b")

    # A pre-existing queued→running send for `a` on this role (never dispatched to
    # the runner, so it stays in-flight for the duration of the test).
    with app.state.db.repos() as repos:
        op = repos.operations.create("send", {"contact_id": a, "job_id": job, "message": "x"})
        repos.operations.mark_running(op.id)

    resp = client.post("/api/referrals/reach-out", headers=AUTH, json={
        "job_id": job,
        "contacts": [{"contact_id": a, "message": "hi a"}, {"contact_id": b, "message": "hi b"}],
    })
    assert resp.status_code == 202
    body = resp.json()
    assert _skipped(body) == [a]
    assert len(body["enqueued"]) == 1
    with app.state.db.repos() as repos:
        enq = repos.operations.get(body["enqueued"][0])
    assert enq.input_snapshot["contact_id"] == b  # only b enqueued


def test_reach_out_dedupes_a_repeated_contact_within_one_request(app_client) -> None:
    """The guard also holds within one request: the same contact listed twice
    enqueues one send and skips the duplicate."""
    app, client = app_client
    _enable_networking(client)
    job = _make_job(client)
    a = _make_contact(client, "a")

    resp = client.post("/api/referrals/reach-out", headers=AUTH, json={
        "job_id": job,
        "contacts": [{"contact_id": a, "message": "hi"}, {"contact_id": a, "message": "hi again"}],
    })
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["enqueued"]) == 1
    assert _skipped(body) == [a]


def test_reach_out_refused_when_networking_disabled(app_client) -> None:
    """The server-side gate refuses the whole route when Referral Outreach is off
    — a client that skips the UI toggle cannot trigger a real send (audit P2-4)."""
    _app, client = app_client
    a = _make_contact(client, "a")  # networking NOT enabled

    resp = client.post("/api/referrals/reach-out", headers=AUTH, json={
        "contacts": [{"contact_id": a, "message": "hi"}],
    })
    assert resp.status_code == 403
    assert "Referral Outreach is disabled" in resp.json()["detail"]


# ── the popup quota reads the same ledger the send path enforces ────────────


def test_quota_snapshot_reads_the_same_ledger_the_send_path_charges(
    tmp_path, monkeypatch
) -> None:
    """The popup's quota snapshot and the send path's enforcement are the SAME
    `Pacer` read of the SAME ledger — they can't diverge. Charge invites through
    the real GPL send path, then read the snapshot the popup uses (`linkedin_
    quota_snapshot`) and a raw ledger read; all three agree."""
    from sidecar.packages.referral_outreach import PacingProfile
    from sidecar.packages.referral_outreach.upstream import actions, pacing, session, worker
    from sidecar.packages.referral_outreach.upstream.pacing import Pacer, resolve_profile

    state = tmp_path / "linkedin" / "state"
    # The snapshot helper reads `linkedin_state_dir()`; point it at the same dir
    # the enforcing send path below writes, then prove they never diverge.
    monkeypatch.setattr(ops, "linkedin_state_dir", lambda: state)
    monkeypatch.setattr(pacing, "send_delay_seconds", lambda: 0.0)
    monkeypatch.setattr(
        actions, "send_connection_request", lambda s, p, note="": ("pending", "")
    )

    class _Inline:
        def __init__(self, **kwargs: object) -> None: ...
        def run_browser(self, action):
            return action()
        def close(self) -> None: ...

    monkeypatch.setattr(session, "AccountSession", _Inline)

    profile = PacingProfile()  # free · 60%, the popup's default
    before = ops.linkedin_quota_snapshot(profile)
    assert before["daily_used"] == 0

    for pid in ("a", "b"):
        out = worker.send_connection(pid, state_dir=str(state))
        assert out["ok"] and out["sent"]

    after = ops.linkedin_quota_snapshot(profile)
    raw = Pacer(resolve_profile(profile), state_dir=state).remaining()
    assert after["daily_used"] == 2                          # the two enforced sends
    assert after["daily_used"] == raw["daily_used"]          # same ledger, same read
    assert after["daily_remaining"] == raw["daily_remaining"]
