"""Covers: A4-followup HTTP surface (JD-gap work).

Route behavior through the real app (TestClient → lifespan → real migration +
runner + seeded-disabled schedules):

- schedule management (A4 flag #2): list, enable/disable + set interval,
  run-now (score_new fan-out; scan single op) — seeded disabled by default;
- Add-by-URL live path (A4 flag #3, US-JB-07): `/api/jobs/preview` extracts
  editable fields (probe faked — no live network), and `POST /api/jobs` persists
  with dedup + tombstone discipline and enqueues a score.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.modules.scraper import ScraperError
from sidecar.modules.scraper.types import NormalizedJob

TOKEN = "test-token-a4b"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _add_job(client: TestClient, url: str, title: str = "T") -> dict[str, object]:
    """Create a job via the Add-by-URL route; return its JobDTO json."""
    return client.post(
        "/api/jobs", headers=AUTH, json={"canonical_url": url, "title": title}
    ).json()


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,  # we drive run-now / ticks explicitly
    )
    with TestClient(app) as client:
        yield app, client


# ---------------------------------------------------------------------------
# schedules (A4 flag #2)
# ---------------------------------------------------------------------------


def test_schedules_seeded_disabled(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.get("/api/schedules", headers=AUTH)
    assert resp.status_code == 200
    kinds = {s["kind"]: s for s in resp.json()}
    # scan/score_new seeded OFF (no unattended LLM spend); cleanup_trash is
    # zero-LLM/zero-network → seeded ON. The networking housekeeping schedules
    # return with the Referral Outreach commits.
    assert set(kinds) == {"scan", "score_new", "cleanup_trash"}
    assert not kinds["scan"]["enabled"]
    assert not kinds["score_new"]["enabled"]
    # FR-SYS-04: zero-LLM/zero-network Trash-TTL eviction → seeded ON.
    assert kinds["cleanup_trash"]["enabled"]


def test_schedule_enable_and_set_interval(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    schedules = client.get("/api/schedules", headers=AUTH).json()
    sched = next(s for s in schedules if s["kind"] == "scan")
    before_due = sched["next_due_at"]

    resp = client.patch(
        f"/api/schedules/{sched['id']}",
        headers=AUTH,
        json={"enabled": True, "interval_minutes": 720},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["interval_minutes"] == 720
    # Enabling a seeded-disabled schedule pulls next_due out of the far future.
    assert body["next_due_at"] < before_due


def test_schedule_patch_unknown_404(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.patch("/api/schedules/nope", headers=AUTH, json={"enabled": True})
    assert resp.status_code == 404


def test_run_score_new_fans_out_per_unscored_job(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    with app.state.db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer.")
        repos.jobs.create(canonical_url="https://ex.co/a", title="A", source_adapter="lever")
        repos.jobs.create(canonical_url="https://ex.co/b", title="B", source_adapter="lever")
    sched = next(
        s for s in client.get("/api/schedules", headers=AUTH).json() if s["kind"] == "score_new"
    )

    resp = client.post(f"/api/schedules/{sched['id']}/run", headers=AUTH)
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["enqueued"]) == 2  # one score op per unscored job
    assert body["schedule"]["last_enqueued_operation_id"] == body["enqueued"][-1]


def test_run_score_new_without_profile_enqueues_nothing(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    sched = next(
        s for s in client.get("/api/schedules", headers=AUTH).json() if s["kind"] == "score_new"
    )
    resp = client.post(f"/api/schedules/{sched['id']}/run", headers=AUTH)
    assert resp.status_code == 202
    assert resp.json()["enqueued"] == []  # planner returns [] with no master profile


def test_run_schedule_unknown_404(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.post("/api/schedules/nope/run", headers=AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Add-by-URL live path (A4 flag #3, US-JB-07)
# ---------------------------------------------------------------------------


def _fake_probe_ok(_url: str, *_a: object, **_k: object) -> NormalizedJob:
    return NormalizedJob(
        title="Staff Engineer",
        canonical_url="https://job-boards.greenhouse.io/acme/jobs/42",
        company="Acme",
        location="Remote",
        description="Build distributed systems in Go and Rust.",
        posted_at="2026-07-01",
        source_adapter="greenhouse",
    )


def test_preview_extracts_editable_fields(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = app_client
    monkeypatch.setattr("sidecar.app.api.routes.probe_url", _fake_probe_ok)
    resp = client.post(
        "/api/jobs/preview", headers=AUTH, json={"url": "https://job-boards.greenhouse.io/acme"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Staff Engineer"
    assert body["company"] == "Acme"
    assert body["description"]
    assert body["source_adapter"] == "greenhouse"


def test_preview_fetch_failure_is_422_verbatim(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = app_client

    def _boom(_url: str, *_a: object, **_k: object) -> NormalizedJob:
        raise ScraperError("fetch", "could not fetch: timed out")

    monkeypatch.setattr("sidecar.app.api.routes.probe_url", _boom)
    resp = client.post("/api/jobs/preview", headers=AUTH, json={"url": "https://x.test/j"})
    assert resp.status_code == 422
    assert "timed out" in resp.json()["detail"]  # verbatim underlying message


def test_create_job_persists_and_enqueues_score(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with app.state.db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer.")

    resp = client.post(
        "/api/jobs",
        headers=AUTH,
        json={
            "canonical_url": "https://job-boards.greenhouse.io/acme/jobs/42?utm_source=x",
            "title": "Staff Engineer",
            "description": "Go and Rust.",
        },
    )
    assert resp.status_code == 201
    job = resp.json()
    # canonical: tracking param stripped, trailing normalized.
    assert job["canonical_url"] == "https://job-boards.greenhouse.io/acme/jobs/42"

    # A score op was enqueued for the new job (US-JB-07 → FR-JB-01 feed sort).
    ops = client.get("/api/operations", headers=AUTH).json()
    assert any(o["kind"] == "score" and o["input_snapshot"].get("job_id") == job["id"] for o in ops)


def test_create_job_dedup_returns_existing(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    payload = {"canonical_url": "https://ex.co/dup", "title": "First"}
    first = client.post("/api/jobs", headers=AUTH, json=payload).json()
    second = client.post(
        "/api/jobs", headers=AUTH, json={"canonical_url": "https://ex.co/dup", "title": "Second"}
    )
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]  # first-seen wins (FR-SYS-01)
    assert second.json()["title"] == "First"  # not overwritten


def test_create_job_tombstoned_is_409(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    with app.state.db.repos() as repos:
        repos.tombstones.create("https://ex.co/gone")
    resp = client.post(
        "/api/jobs", headers=AUTH, json={"canonical_url": "https://ex.co/gone", "title": "X"}
    )
    assert resp.status_code == 409
    # A tombstone is final — honest copy that re-add is impossible (2026-07-09).
    assert "permanently deleted" in resp.json()["detail"]
    assert "can't be re-added" in resp.json()["detail"]


def test_preview_tombstoned_is_409(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    with app.state.db.repos() as repos:
        repos.tombstones.create("https://ex.co/gone")
    # Preview fails fast (before any network probe) with the same honest copy.
    resp = client.post("/api/jobs/preview", headers=AUTH, json={"url": "https://ex.co/gone"})
    assert resp.status_code == 409
    assert "permanently deleted" in resp.json()["detail"]


def test_add_by_url_restores_trashed_job_keeping_score(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Trash is recoverable: re-adding a Trashed URL un-trashes the SAME row and
    keeps its score/history — no duplicate, no re-score (US-JB-07, 2026-07-09)."""
    app, client = app_client
    payload = {"canonical_url": "https://ex.co/back", "title": "Backend Engineer"}
    created = client.post("/api/jobs", headers=AUTH, json=payload).json()
    job_id = created["id"]
    # Give it a cached score + move it to Trash.
    with app.state.db.repos() as repos:
        repos.job_scores.upsert(job_id=job_id, profile_version=1, score_0_100=88)
        repos.jobs.set_trash_state(job_id, trashed=True)

    resp = client.post("/api/jobs", headers=AUTH, json=payload)
    assert resp.status_code == 201
    restored = resp.json()
    assert restored["id"] == job_id  # same row, not a duplicate
    assert restored["feed_state"] == "active"  # un-trashed
    assert restored["score"]["score_0_100"] == 88  # score preserved

    # No fresh score op enqueued (history kept, not re-scored).
    ops = client.get("/api/operations", headers=AUTH).json()
    assert not any(
        o["kind"] == "score" and o["input_snapshot"].get("job_id") == job_id for o in ops
    )


def test_add_by_url_restores_unscored_trashed_job_and_rescores(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """A `Score failed`/unscored Trashed job re-scores on restore — the US-JB-06
    retry path (Remove → Add back by URL) still works under the new semantics."""
    app, client = app_client
    with app.state.db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer.")
    payload = {"canonical_url": "https://ex.co/unscored", "title": "Eng"}
    created = client.post("/api/jobs", headers=AUTH, json=payload).json()
    job_id = created["id"]
    with app.state.db.repos() as repos:
        repos.jobs.set_trash_state(job_id, trashed=True)  # no cached score

    resp = client.post("/api/jobs", headers=AUTH, json=payload)
    assert resp.status_code == 201
    assert resp.json()["feed_state"] == "active"
    # A score op WAS enqueued (unscored → re-score on restore).
    ops = client.get("/api/operations", headers=AUTH).json()
    assert any(o["kind"] == "score" and o["input_snapshot"].get("job_id") == job_id for o in ops)


def test_empty_trash_tombstones_and_removes(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    a = _add_job(client, "https://ex.co/a")
    b = _add_job(client, "https://ex.co/b")
    client.patch(f"/api/jobs/{a['id']}", headers=AUTH, json={"feed_state": "removed"})
    client.patch(f"/api/jobs/{b['id']}", headers=AUTH, json={"feed_state": "removed"})

    resp = client.post("/api/jobs/trash/empty", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["tombstoned"] == 2

    # Rows gone; both URLs tombstoned so re-add is refused.
    with app.state.db.repos() as repos:
        assert repos.jobs.get(a["id"]) is None
        assert repos.tombstones.exists("https://ex.co/a")
    refused = client.post(
        "/api/jobs", headers=AUTH, json={"canonical_url": "https://ex.co/a", "title": "A"}
    )
    assert refused.status_code == 409


def test_delete_forever_tombstones_single_job(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    j = _add_job(client, "https://ex.co/one", "One")
    client.patch(f"/api/jobs/{j['id']}", headers=AUTH, json={"feed_state": "removed"})

    resp = client.post(f"/api/jobs/{j['id']}/tombstone", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["canonical_urls"] == ["https://ex.co/one"]
    with app.state.db.repos() as repos:
        assert repos.jobs.get(j["id"]) is None
        assert repos.tombstones.exists("https://ex.co/one")


def test_delete_forever_unknown_job_404(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    assert client.post("/api/jobs/nope/tombstone", headers=AUTH).status_code == 404


def test_preview_existing_job_returns_stored_fields(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """A URL already in our store is "fetched back" from the DB — no network
    probe (probe is NOT faked here, so a probe would blow up)."""
    _app, client = app_client
    client.post(
        "/api/jobs",
        headers=AUTH,
        json={"canonical_url": "https://ex.co/known", "title": "Known Role", "company": "KnownCo"},
    )
    resp = client.post("/api/jobs/preview", headers=AUTH, json={"url": "https://ex.co/known"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Known Role"
    assert resp.json()["company"] == "KnownCo"


def test_ttl_tick_tombstones_stale_trash_only(app_client: tuple[FastAPI, TestClient]) -> None:
    """FR-SYS-04: Trash past the 7-day TTL is tombstoned; fresh Trash survives."""
    from datetime import timedelta

    from sidecar.app.db.base import now_utc
    from sidecar.app.registry.persistence import evict_stale_trash

    app, client = app_client
    fresh = _add_job(client, "https://ex.co/fresh")
    stale = _add_job(client, "https://ex.co/stale")
    with app.state.db.repos() as repos:
        repos.jobs.set_trash_state(fresh["id"], trashed=True)  # trashed_at = now
        repos.jobs.set_trash_state(stale["id"], trashed=True, now=now_utc() - timedelta(days=8))

    tombstoned = evict_stale_trash(app.state.db)
    assert stale["id"] in tombstoned
    assert fresh["id"] not in tombstoned
    with app.state.db.repos() as repos:
        assert repos.jobs.get(stale["id"]) is None
        assert repos.tombstones.exists("https://ex.co/stale")
        assert repos.jobs.get(fresh["id"]) is not None
        assert not repos.tombstones.exists("https://ex.co/fresh")


def test_ttl_tick_backfills_legacy_trash_without_stamp(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """A row Trashed before the TTL bookkeeping existed (no `trashed_at`) starts
    its clock on first sight — it is NOT tombstoned immediately."""
    from sidecar.app.registry.persistence import evict_stale_trash

    app, client = app_client
    j = _add_job(client, "https://ex.co/legacy")
    # Simulate legacy: feed_state removed but no source_meta stamp.
    with app.state.db.repos() as repos:
        repos.jobs.update(j["id"], feed_state="removed", source_meta=None)

    assert evict_stale_trash(app.state.db) == []  # backfilled, not tombstoned
    with app.state.db.repos() as repos:
        job = repos.jobs.get(j["id"])
        assert job is not None
        assert job.source_meta is not None
        assert "trashed_at" in job.source_meta


def test_retry_operation_reenqueues_same_kind(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """US-LOG-01 Retry: re-enqueue a failed op with its original input snapshot."""
    _app, client = app_client
    r = client.post("/api/operations/score", json={"job_id": "nope"}, headers=AUTH)
    assert r.status_code == 202
    op_id = r.json()["id"]
    retry = client.post(f"/api/operations/{op_id}/retry", headers=AUTH)
    assert retry.status_code == 202
    body = retry.json()
    assert body["kind"] == "score" and body["id"] != op_id

    # An unknown operation id → 404.
    assert client.post("/api/operations/missing/retry", headers=AUTH).status_code == 404


def test_retry_stamps_failed_row_with_retried_as(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """A retried FAILED row carries `result_ref.retried_as` → the ledger renders
    it as "Retried" instead of a permanently nagging red row (2026-07-12).

    The rows are created directly in the DB (never submitted to the live
    runner) so their terminal states are OURS — an earlier version raced the
    runner's own transitions and flaked."""
    app, client = app_client
    with app.state.db.repos() as repos:
        op = repos.operations.create("score", {"job_id": "nope"})
        op.state = "failed"
        op.error = "engine error: 429"
        op_id = op.id
    retry = client.post(f"/api/operations/{op_id}/retry", headers=AUTH)
    assert retry.status_code == 202
    new_id = retry.json()["id"]
    ops = client.get("/api/operations", headers=AUTH).json()
    old = next(o for o in ops if o["id"] == op_id)
    assert old["result_ref"]["retried_as"] == new_id
    # A SUCCEEDED row is never stamped (retrying it is allowed but not linked).
    with app.state.db.repos() as repos:
        row = repos.operations.create("score", {"job_id": "nope2"})
        row.state = "succeeded"
        op2 = row.id
    client.post(f"/api/operations/{op2}/retry", headers=AUTH)
    ops = client.get("/api/operations", headers=AUTH).json()
    row2 = next(o for o in ops if o["id"] == op2)
    assert not (row2.get("result_ref") or {}).get("retried_as")


def test_scrape_cadence_enables_scan_schedule(app_client: tuple[FastAPI, TestClient]) -> None:
    """Audit P0-1 (2026-07-12): saving a scrape cadence must enable + retime the
    seeded-disabled scan schedule — before this, the wizard collected a cadence
    but nothing consumed it, so a fresh install never background-scraped."""
    _app, client = app_client
    r = client.post(
        "/api/settings", headers=AUTH, json={"ui_state": {"scrape_cadence": "Every 6h"}}
    )
    assert r.status_code == 200
    scheds = client.get("/api/schedules", headers=AUTH).json()
    scan = next(s for s in scheds if s["kind"] == "scan")
    assert scan["enabled"] is True
    assert scan["interval_minutes"] == 360
    # score_new stays seeded-disabled on purpose: the runner's scan→score chain
    # already scores new jobs; enabling both would double-score (audit P1-2).
    score_new = next(s for s in scheds if s["kind"] == "score_new")
    assert score_new["enabled"] is False
    # An unrecognized label never touches the schedule.
    client.post("/api/settings", headers=AUTH, json={"ui_state": {"scrape_cadence": "sometimes"}})
    scan = next(
        s for s in client.get("/api/schedules", headers=AUTH).json() if s["kind"] == "scan"
    )
    assert scan["enabled"] is True and scan["interval_minutes"] == 360

