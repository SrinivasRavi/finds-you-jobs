"""Covers: the find-referrals candidates endpoint surfaces the LAST discover's
outcome so a reopened popup recovers a background (Save-triggered) discover
instead of a blank start screen (2026-07-17 dogfood: a Save-discover that
needed company confirmation, or found nobody, left no visible trace).

ZERO live LinkedIn: discover runs through the FakeVoyagerDriver seam (the wire
stays cold); the whole flow goes through the real HTTP routes + runner.
"""

from __future__ import annotations

import time
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.registry import networker_ops as ops

from ..modules.networker.fakes import FakeVoyagerDriver

TOKEN = "test-token-refrec"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}

_RESOLVE_AMBIGUOUS = {
    "op": "resolve-company",
    "ok": True,
    "companies": [
        {"urn": "urn:li:fsd_company:1", "company_id": "1", "name": "Okta", "vanity": "okta",
         "website": "", "domain_match": False},
        {"urn": "urn:li:fsd_company:2", "company_id": "2", "name": "Okta Inc", "vanity": "okta-inc",
         "website": "", "domain_match": False},
    ],
}
_DISCOVER_ROWS = {
    "op": "discover", "ok": True, "contacts": [
        {"public_identifier": "ada-ok", "full_name": "Ada OK", "current_title": "SWE",
         "current_company": "Okta", "url": "https://www.linkedin.com/in/ada-ok",
         "connection_degree": 2},
    ],
}
_DISCOVER_EMPTY = {"op": "discover", "ok": True, "contacts": []}


def _make_client(
    tmp_path: Path, driver: FakeVoyagerDriver
) -> Generator[tuple[FastAPI, TestClient]]:
    original = ops.DRIVER_FACTORY
    ops.DRIVER_FACTORY = lambda tier: driver
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    try:
        with TestClient(app) as client:
            yield app, client
    finally:
        ops.DRIVER_FACTORY = original


def _enable_and_seed_job(client: TestClient) -> str:
    client.post("/api/settings", headers=AUTH, json={"voyager_risk_marker_on": True})
    client.post("/api/profile", headers=AUTH, json={"resume_markdown": "# C\n\nBackend."})
    job = client.post(
        "/api/jobs", headers=AUTH,
        json={"canonical_url": "https://ex.co/j/okta", "title": "Staff Eng",
              "company": "Okta", "location": "Remote", "description": "d" * 60,
              "source_adapter": "greenhouse"},
    ).json()
    return job["id"]


def _wait_op(client: TestClient, job_id: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ops_list = client.get("/api/operations?limit=50", headers=AUTH).json()
        disc = [o for o in ops_list if o["kind"] == "discover"]
        if disc and all(o["state"] in ("succeeded", "failed") for o in disc):
            return
        time.sleep(0.1)
    raise AssertionError("discover never settled")


@pytest.fixture
def confirm_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _make_client(tmp_path, FakeVoyagerDriver(resolve_result=_RESOLVE_AMBIGUOUS))


@pytest.fixture
def found_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _make_client(
        tmp_path,
        FakeVoyagerDriver(
            resolve_result={"op": "resolve-company", "ok": True, "companies": [
                {"urn": "urn:li:fsd_company:9", "company_id": "9", "name": "Okta", "vanity": "okta",
                 "website": "okta.com", "domain_match": True}]},
            discover_result=_DISCOVER_ROWS,
        ),
    )


@pytest.fixture
def empty_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _make_client(
        tmp_path,
        FakeVoyagerDriver(
            resolve_result={"op": "resolve-company", "ok": True, "companies": [
                {"urn": "urn:li:fsd_company:9", "company_id": "9", "name": "Okta", "vanity": "okta",
                 "website": "okta.com", "domain_match": True}]},
            discover_result=_DISCOVER_EMPTY,
        ),
    )


def test_candidates_state_never_before_discover(confirm_client) -> None:
    _app, client = confirm_client
    job_id = _enable_and_seed_job(client)
    body = client.get(f"/api/jobs/{job_id}/referrals/candidates", headers=AUTH).json()
    assert body["discover_state"] == "never"
    assert body["candidates"] == []


def test_background_discover_needing_confirm_is_recoverable(confirm_client) -> None:
    _app, client = confirm_client
    job_id = _enable_and_seed_job(client)
    # A background (Save-style) discover with no company_urn → ambiguous → the op
    # succeeds returning needs_company_confirm, persisting no contacts.
    client.post(f"/api/jobs/{job_id}/referrals/discover", headers=AUTH, json={})
    _wait_op(client, job_id)

    body = client.get(f"/api/jobs/{job_id}/referrals/candidates", headers=AUTH).json()
    assert body["discover_state"] == "confirm"
    assert body["candidates"] == []
    # The entity choices are recovered so the reopened popup shows the picker.
    names = {c["name"] for c in body["company_confirm"]}
    assert names == {"Okta", "Okta Inc"}


def test_discover_that_found_people_reports_found(found_client) -> None:
    _app, client = found_client
    job_id = _enable_and_seed_job(client)
    client.post(f"/api/jobs/{job_id}/referrals/discover", headers=AUTH, json={})
    _wait_op(client, job_id)

    body = client.get(f"/api/jobs/{job_id}/referrals/candidates", headers=AUTH).json()
    assert body["discover_state"] == "found"
    assert [c["name"] for c in body["candidates"]] == ["Ada OK"]


def test_discover_that_found_nobody_reports_empty(empty_client) -> None:
    _app, client = empty_client
    job_id = _enable_and_seed_job(client)
    client.post(f"/api/jobs/{job_id}/referrals/discover", headers=AUTH, json={})
    _wait_op(client, job_id)

    body = client.get(f"/api/jobs/{job_id}/referrals/candidates", headers=AUTH).json()
    assert body["discover_state"] == "empty"
    assert body["candidates"] == []


_REFUSAL_REASON = "profile_views cap reached (87/day, plan=sales_navigator)"


@pytest.fixture
def refused_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _make_client(
        tmp_path,
        FakeVoyagerDriver(
            resolve_result={"op": "resolve-company", "ok": True, "companies": [
                {"urn": "urn:li:fsd_company:9", "company_id": "9", "name": "Okta", "vanity": "okta",
                 "website": "okta.com", "domain_match": True}]},
            discover_result={"op": "discover", "ok": False, "error": "cap_or_backoff",
                             "reason": _REFUSAL_REASON, "count": 0, "contacts": []},
        ),
    )


def test_discover_refused_reports_refused_with_verbatim_reason(refused_client) -> None:
    # The Kaseya 2026-08-15 bug: a cap/backoff refusal rendered as `empty`
    # ("No contacts found at this company yet"). The endpoint must recover the
    # refusal with its verbatim pacer reason.
    _app, client = refused_client
    job_id = _enable_and_seed_job(client)
    client.post(f"/api/jobs/{job_id}/referrals/discover", headers=AUTH, json={})
    _wait_op(client, job_id)

    body = client.get(f"/api/jobs/{job_id}/referrals/candidates", headers=AUTH).json()
    assert body["discover_state"] == "refused"
    assert body["refusal_reason"] == _REFUSAL_REASON
    assert body["candidates"] == []


def test_in_flight_sends_ride_the_candidates_payload(confirm_client) -> None:
    """A queued/running send op's contact rides `in_flight_contact_ids`, so the
    popup's per-row Sending state survives a close/reopen (2026-08-16: it used
    to live only in modal state — leaving and coming back showed Connect again
    while the referral outreach agent was still mid-send)."""
    _app, client = confirm_client
    job_id = _enable_and_seed_job(client)
    contact = client.post("/api/contacts", headers=AUTH, json={
        "linkedin_url": "https://www.linkedin.com/in/inflight-fixture",
        "name": "Inflight Fixture", "current_company": "Okta",
        "connection_status": "candidate",
    }).json()

    # An in-flight send op planted directly (not via the runner, so it stays
    # queued for the read below).
    with _app.state.db.repos() as repos:
        repos.operations.create("send", {"contact_id": contact["id"], "job_id": job_id})

    body = client.get(f"/api/jobs/{job_id}/referrals/candidates", headers=AUTH).json()
    assert body["in_flight_contact_ids"] == [contact["id"]]
