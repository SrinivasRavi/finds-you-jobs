"""What the user is told when a LinkedIn operation fails.

From the 2026-09-20 failure-mode audit. Each test pins one state the add-on can
land in and asserts the state reaches the user, because the defects it found were
never silent crashes — they were failures the app absorbed and reported as
nothing, or as success.

ZERO live LinkedIn traffic: the pacing ledger is a real file under `tmp_path` and
every driver is the `DRIVER_FACTORY` fake. The wire stays cold.
"""

from __future__ import annotations

import time
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.main import create_app
from sidecar.app.registry import networker_ops as ops
from sidecar.modules.networker.types import NetworkerError

from ..modules.networker.fakes import FakeVoyagerDriver
from .conftest import migrated_db  # noqa: F401 — fixture
from .test_networker_ops_n3 import DISCOVER_ROWS, Wired, _ctx, _nn, _seed, wired  # noqa: F401

TOKEN = "test-token-lifm"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# ── the backoff a read path caused still reaches the header ─────────────────


def _make_client(tmp_path: Path) -> Generator[tuple[FastAPI, TestClient]]:
    original = ops.DRIVER_FACTORY
    ops.DRIVER_FACTORY = lambda profile: FakeVoyagerDriver()
    app = create_app(token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
                     enable_scheduler=False)
    try:
        with TestClient(app) as client:
            yield app, client
    finally:
        ops.DRIVER_FACTORY = original


@pytest.fixture
def own_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A pacing ledger of this test's own.

    `conftest` points `FYJ_DATA_DIR` at ONE tmp dir for the whole session, so
    `linkedin_state_dir()` is shared by every test in a run and a pause written
    by one is still there for the next. These tests write pauses deliberately,
    so they get their own file or they read each other's.
    """
    state = tmp_path / "ledger"
    monkeypatch.setattr(ops, "linkedin_state_dir", lambda: state)
    return state


@pytest.fixture
def app_client(
    tmp_path: Path, own_ledger: Path,  # noqa: ARG001 — isolation only
) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _make_client(tmp_path)


def _enable_networking(client: TestClient) -> None:
    resp = client.post("/api/settings", headers=AUTH, json={"voyager_risk_marker_on": True})
    assert resp.status_code == 200


def _connect(client: TestClient) -> None:
    """Mark the session valid without touching LinkedIn, so the pill starts
    from `valid` rather than `never_set`."""
    with _app_db(client).repos() as repos:
        repos.linkedin_session.update(status="valid", connected_as="Ada Lovelace")


def _pause_the_pacer(client: TestClient, reason: str) -> None:
    """Enter backoff the way a 429 does: through the ledger the worker charges.

    Deliberately NOT through the send path, because the bug under test is that
    only the send path mirrored the pause onto the session row.
    """
    from sidecar.packages.referral_outreach import resolve_profile
    from sidecar.packages.referral_outreach.facade import Pacer

    with _app_db(client).repos() as repos:
        profile = ops.resolve_pacing_profile(repos)
    pacer = Pacer(resolve_profile(profile), state_dir=ops.linkedin_state_dir())
    pacer.pause_for_backoff(reason)
    pacer.save()


def _app_db(client: TestClient) -> Database:
    return client.app.state.db  # type: ignore[attr-defined,no-any-return]


def test_a_rate_limit_outside_the_send_path_still_shows_as_backing_off(
    app_client
) -> None:
    """The gap: `backing_off` was written only by `send_entrypoint`, so a 429
    during discover or contact sync paused every meter while the header kept
    reading "LinkedIn connected" and Settings offered no Resume button."""
    _app, client = app_client
    _enable_networking(client)
    _connect(client)

    _pause_the_pacer(client, "LinkedIn returned HTTP 429 (throttled/blocked)")

    body = client.get("/api/linkedin/session", headers=AUTH).json()
    assert body["status"] == "backing_off"
    assert "429" in body["paused_reason"]
    assert body["paused_until"] is not None


def test_a_never_connected_session_is_never_reported_as_backing_off(app_client) -> None:
    """A pause is a statement about the account's traffic. With no session at
    all the honest state is still `never_set`, so the user is asked to connect
    rather than told to wait out a backoff they cannot be in."""
    _app, client = app_client
    _enable_networking(client)

    _pause_the_pacer(client, "LinkedIn returned HTTP 429 (throttled/blocked)")

    body = client.get("/api/linkedin/session", headers=AUTH).json()
    assert body["status"] == "never_set"


def test_the_backoff_clears_from_the_same_ledger_it_was_read_from(app_client) -> None:
    """An expired pause reports as expired without anyone writing the row back:
    the deadline is in the ledger, so time passing is enough."""
    _app, client = app_client
    _enable_networking(client)
    _connect(client)

    from sidecar.packages.referral_outreach import resolve_profile
    from sidecar.packages.referral_outreach.facade import Pacer

    with _app_db(client).repos() as repos:
        profile = ops.resolve_pacing_profile(repos)
    pacer = Pacer(resolve_profile(profile), state_dir=ops.linkedin_state_dir())
    pacer.state.paused_until = time.time() - 1.0  # a pause that has run out
    pacer.state.paused_reason = "stale"
    pacer.save()

    body = client.get("/api/linkedin/session", headers=AUTH).json()
    assert body["status"] != "backing_off"


# ── a send that fails outside NetworkerError still leaves a trail ───────────


@pytest.mark.parametrize(
    ("exc", "label"),
    [
        (RuntimeError("chrome failed to launch within 60s"), "browser launch"),
        (OSError("LinkedIn API error 500: service unavailable"), "an exhausted 5xx"),
        (NetworkerError("voyager", "stale selector"), "a voyager failure"),
    ],
)
def test_a_failed_send_always_writes_its_audit_row(
    wired: Wired, exc: Exception, label: str  # noqa: F811
) -> None:
    """Every send attempt owes an OutreachLog row (FR-REF / NFR-SIDE-04).

    Only `NetworkerError` was caught until 2026-09-20, and the driver wraps
    nothing else, so a browser that never launched (RuntimeError) or an
    exhausted 5xx retry (OSError) left no row, emitted no `send_failed`, and
    left the referrals modal spinning on "Sending" until it was closed.
    """
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired, with_job=False)
    with wired.db.repos() as repos:
        cid = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/sarah-tan")).id

    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(
        raise_on="send_connection", error=exc,
    )
    events: list[dict] = []
    with pytest.raises(type(exc)):
        ops.send_entrypoint(
            _ctx(wired.db, "send", {"contact_id": cid, "message": "Hi"}, events=events)
        )

    with wired.db.repos() as repos:
        logs = repos.outreach_logs.list_for_contact(cid)
        assert logs, f"{label}: no audit row for a failed send"
        assert logs[0].outcome == "failed"
        assert str(exc) in logs[0].outcome_detail  # verbatim

    failed = [e for e in events if e.get("payload", {}).get("phase") == "send_failed"]
    assert failed, f"{label}: no send_failed event, so the modal never stops spinning"


def test_a_failed_send_still_closes_its_browser(wired: Wired) -> None:  # noqa: F811
    """A launch failure must not leak the driver either — S-C12's property,
    now over the widened catch."""
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired, with_job=False)
    with wired.db.repos() as repos:
        cid = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/sarah-tan")).id

    built: list[FakeVoyagerDriver] = []

    def factory(_tier):
        drv = FakeVoyagerDriver(
            raise_on="send_connection", error=RuntimeError("chrome failed to launch"),
        )
        built.append(drv)
        return drv

    ops.DRIVER_FACTORY = factory
    with pytest.raises(RuntimeError):
        ops.send_entrypoint(_ctx(wired.db, "send", {"contact_id": cid, "message": "Hi"}))

    assert len(built) == 1 and built[0].closed
