"""Board correctness + Expired aging (this round's board work package).

Covers:
- Score-failed derivation (FR-JB-07 / NFR-OFFLINE-02): a failed `score` op with
  no cached score resolves the board row to `scoreStatus == "failed"`.
- Board pagination + honest total (FR-JB-02): 50/page, `total` counts every
  eligible row — no silent 200-row cap.
- Real last-scan meta + explained empty/running/error status (FR-JB-10).
- Board search (FR-JB-13 / US-JB-12): shallow list_q vs deep text_q, filtered
  server-side before pagination; a search miss never masquerades as an empty
  scrape.
- Expired aging (FR-SYS-03): grey at 14d, hard-delete (no tombstone) at 30d,
  rescued by Save, un-expire resets the timer, legacy rows backfilled once.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.db.base import now_utc
from sidecar.app.main import create_app
from sidecar.app.registry.persistence import age_expired_jobs

TOKEN = "test-token-board"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


def _db(app: FastAPI) -> Database:
    return app.state.db  # type: ignore[no-any-return]


def _job(repos: object, job_id: str):  # type: ignore[no-untyped-def]
    j = repos.jobs.get(job_id)  # type: ignore[attr-defined]
    assert j is not None
    return j


# ---------------------------------------------------------------------------
# Score-failed derivation (FR-JB-07 / NFR-OFFLINE-02)
# ---------------------------------------------------------------------------


def test_board_marks_score_failed_when_score_op_failed(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        job = repos.jobs.create(canonical_url="u1", title="A", source_adapter="lever")
        op = repos.operations.create("score", {"job_id": job.id})
        repos.operations.mark_failed(op.id, error="no connectivity")
        job_id = job.id

    rows = {j["id"]: j for j in client.get("/api/board", headers=AUTH).json()["jobs"]}
    assert rows[job_id]["scoreStatus"] == "failed"  # never a perpetual Pending
    assert rows[job_id]["score"] is None


def test_board_pending_when_no_score_op(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        job = repos.jobs.create(canonical_url="u1", title="A", source_adapter="lever")
        job_id = job.id
    rows = {j["id"]: j for j in client.get("/api/board", headers=AUTH).json()["jobs"]}
    assert rows[job_id]["scoreStatus"] == "pending"


# ---------------------------------------------------------------------------
# GET /api/scan/progress — board-level scan + scoring progress (observed-issue
# #2 backend). Derived entirely from the operations ledger; no schema change.
# ---------------------------------------------------------------------------


def test_scan_progress_reports_batch_counts(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        j1 = repos.jobs.create(canonical_url="s1", title="A", source_adapter="lever")
        j2 = repos.jobs.create(canonical_url="s2", title="B", source_adapter="lever")
        j3 = repos.jobs.create(canonical_url="s3", title="C", source_adapter="lever")
        old = repos.jobs.create(canonical_url="s-old", title="Old", source_adapter="lever")
        scan = repos.operations.create("scan", {})
        repos.operations.mark_succeeded(
            scan.id, result_ref={"scan": {"new_job_ids": [j1.id, j2.id, j3.id]}}
        )
        # j1 done (succeeded), j2 done (failed), j3 still queued (pending).
        repos.operations.mark_succeeded(
            repos.operations.create("score", {"job_id": j1.id}).id
        )
        repos.operations.mark_failed(
            repos.operations.create("score", {"job_id": j2.id}).id, error="x"
        )
        repos.operations.create("score", {"job_id": j3.id})  # queued
        # An older job's score op — outside this scan's batch, never counted in
        # score_done (though it is terminal, its job isn't in new_job_ids).
        repos.operations.mark_succeeded(
            repos.operations.create("score", {"job_id": old.id}).id
        )

    body = client.get("/api/scan/progress", headers=AUTH).json()
    assert body["scan_running"] is False
    assert body["last_scan_at"] is not None
    assert body["new_found"] == 3
    # score_pending is the GLOBAL live count — just j3's queued op here.
    assert body["score_pending"] == 1
    # score_done is batch-scoped: j1 + j2 terminal; j3 still pending; old excluded.
    assert body["score_done"] == 2


def test_scan_progress_flags_running_scan(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        repos.operations.create("scan", {})  # queued → in-flight, never pumped
    body = client.get("/api/scan/progress", headers=AUTH).json()
    assert body["scan_running"] is True
    assert body["last_scan_at"] is None  # no succeeded scan yet
    assert body["new_found"] == 0
    assert body["score_pending"] == 0
    assert body["score_done"] == 0


def test_scan_progress_requires_token(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    assert client.get("/api/scan/progress").status_code == 401


# ---------------------------------------------------------------------------
# Pagination + total (FR-JB-02)
# ---------------------------------------------------------------------------


def test_board_paginates_with_honest_total(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        for i in range(60):
            repos.jobs.create(
                canonical_url=f"u{i}", title=f"J{i}", source_adapter="lever"
            )

    page0 = client.get("/api/board?page=0", headers=AUTH).json()
    assert page0["total"] == 60
    assert page0["pageSize"] == 50
    assert len(page0["jobs"]) == 50

    page1 = client.get("/api/board?page=1", headers=AUTH).json()
    assert page1["total"] == 60
    assert len(page1["jobs"]) == 10  # the remainder — nothing silently truncated


def _seed_search_jobs(app: FastAPI) -> dict[str, str]:
    """Two distinguishable jobs; the second carries a score whose breakdown
    mentions 'kubernetes' so text_q can prove it searches score texts."""
    with _db(app).repos() as repos:
        j1 = repos.jobs.create(
            canonical_url="u-stripe",
            title="Backend Engineer",
            company="Stripe",
            location="Remote",
            description="Build payments infrastructure in Go.",
            source_adapter="lever",
        )
        j2 = repos.jobs.create(
            canonical_url="u-acme",
            title="Platform Engineer",
            company="Acme",
            location="Berlin",
            description="Own the deployment pipeline.",
            source_adapter="greenhouse",
        )
        repos.job_scores.create(
            job_id=j2.id,
            profile_version=1,
            score_0_100=77,
            reasons=["Strong infra match"],
            breakdown_md="Deep kubernetes experience matches the JD.",
        )
        return {"stripe": j1.id, "acme": j2.id}


def test_board_list_q_matches_title_company_location_only(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    ids = _seed_search_jobs(app)

    body = client.get("/api/board?list_q=stripe", headers=AUTH).json()
    assert [j["id"] for j in body["jobs"]] == [ids["stripe"]]
    assert body["total"] == 1  # total reflects the filtered count

    # list_q is shallow: JD text ('pipeline') must NOT match.
    body = client.get("/api/board?list_q=pipeline", headers=AUTH).json()
    assert body["jobs"] == []
    assert body["total"] == 0

    # Clearing the search restores the full feed.
    body = client.get("/api/board", headers=AUTH).json()
    assert body["total"] == 2


def test_board_text_q_matches_jd_and_score_texts(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    ids = _seed_search_jobs(app)

    # JD body text.
    body = client.get("/api/board?text_q=payments", headers=AUTH).json()
    assert [j["id"] for j in body["jobs"]] == [ids["stripe"]]

    # Match-score breakdown text.
    body = client.get("/api/board?text_q=kubernetes", headers=AUTH).json()
    assert [j["id"] for j in body["jobs"]] == [ids["acme"]]

    # Match-score reasons text.
    body = client.get("/api/board?text_q=infra+match", headers=AUTH).json()
    assert [j["id"] for j in body["jobs"]] == [ids["acme"]]

    # Case-insensitive.
    body = client.get("/api/board?text_q=STRIPE", headers=AUTH).json()
    assert [j["id"] for j in body["jobs"]] == [ids["stripe"]]


def test_board_search_miss_keeps_scan_status_honest(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """A search that matches nothing is a filter miss, not an empty scrape —
    scanStatus must stay 'idle' so the UI can say 'no match' not 'no jobs'."""
    app, client = app_client
    _seed_search_jobs(app)
    body = client.get("/api/board?text_q=zzz-no-such-term", headers=AUTH).json()
    assert body["jobs"] == []
    assert body["total"] == 0
    assert body["scanStatus"] == "idle"


def test_board_search_filters_before_pagination(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Matches beyond page 0 of the unfiltered feed are still found (the whole
    point of server-side search vs. filtering loaded pages client-side)."""
    app, client = app_client
    with _db(app).repos() as repos:
        for i in range(60):
            repos.jobs.create(
                canonical_url=f"u{i}", title=f"J{i}", source_adapter="lever"
            )
        needle = repos.jobs.create(
            canonical_url="u-needle",
            title="Needle Role",
            company="Haystack",
            source_adapter="lever",
        )
        needle_id = needle.id

    body = client.get("/api/board?list_q=needle", headers=AUTH).json()
    assert [j["id"] for j in body["jobs"]] == [needle_id]
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# Last-scan meta + explained status (FR-JB-10)
# ---------------------------------------------------------------------------


def test_board_scan_status_empty_then_error_then_running(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    # No jobs, no scan → empty.
    body = client.get("/api/board", headers=AUTH).json()
    assert body["scanStatus"] == "empty"
    assert body["lastScanAt"] is None

    # A failed scan → error + verbatim message + no success time.
    with _db(app).repos() as repos:
        op = repos.operations.create("scan", {})
        repos.operations.mark_failed(op.id, error="dns failure")
    body = client.get("/api/board", headers=AUTH).json()
    assert body["scanStatus"] == "error"
    assert body["scanError"] == "dns failure"

    # A newer running scan wins → running.
    with _db(app).repos() as repos:
        repos.operations.create("scan", {})  # queued
    body = client.get("/api/board", headers=AUTH).json()
    assert body["scanStatus"] == "running"


def test_board_last_scan_at_from_succeeded_scan(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        repos.jobs.create(canonical_url="u1", title="A", source_adapter="lever")
        op = repos.operations.create("scan", {})
        repos.operations.mark_succeeded(op.id, result_ref={"scan": {}})
    body = client.get("/api/board", headers=AUTH).json()
    assert body["scanStatus"] == "idle"  # rows present
    assert body["lastScanAt"] is not None


# ---------------------------------------------------------------------------
# Expired aging (FR-SYS-03)
# ---------------------------------------------------------------------------


def test_active_greys_to_expired_at_14_days(migrated_db: Database) -> None:
    db = migrated_db
    now = now_utc()
    with db.repos() as repos:
        fresh = repos.jobs.create(
            canonical_url="fresh", title="F", source_adapter="lever",
            ingested_at=now - timedelta(days=10),
        )
        old = repos.jobs.create(
            canonical_url="old", title="O", source_adapter="lever",
            ingested_at=now - timedelta(days=20),
        )
        fresh_id, old_id = fresh.id, old.id

    result = age_expired_jobs(db, now=now)
    assert result["expired"] == [old_id]
    with db.repos() as repos:
        assert _job(repos, old_id).feed_state == "expired"
        assert (_job(repos, old_id).source_meta or {}).get("expired_at") is not None
        assert _job(repos, fresh_id).feed_state == "active"  # under 14 days


def test_expired_hard_deleted_at_30_days_without_tombstone(
    migrated_db: Database,
) -> None:
    db = migrated_db
    now = now_utc()
    with db.repos() as repos:
        job = repos.jobs.create(canonical_url="stale", title="S", source_adapter="lever")
        repos.jobs.update(
            job.id,
            feed_state="expired",
            source_meta={"expired_at": (now - timedelta(days=31)).isoformat()},
        )
        job_id, url = job.id, job.canonical_url

    result = age_expired_jobs(db, now=now)
    assert result["deleted"] == [job_id]
    with db.repos() as repos:
        assert repos.jobs.get(job_id) is None
        # FR-SYS-03: no tombstone — a later scrape may re-surface the posting.
        assert repos.tombstones.exists(url) is False


def test_expired_legacy_row_without_stamp_backfilled_not_deleted(
    migrated_db: Database,
) -> None:
    db = migrated_db
    now = now_utc()
    with db.repos() as repos:
        job = repos.jobs.create(canonical_url="legacy", title="L", source_adapter="lever")
        repos.jobs.update(job.id, feed_state="expired", source_meta=None)
        job_id = job.id
    result = age_expired_jobs(db, now=now)
    assert result["deleted"] == []  # clock starts now, not deleted this tick
    with db.repos() as repos:
        assert repos.jobs.get(job_id) is not None
        assert (_job(repos, job_id).source_meta or {}).get("expired_at") is not None


def test_unexpire_resets_the_14_day_timer(migrated_db: Database) -> None:
    db = migrated_db
    now = now_utc()
    with db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="u", title="U", source_adapter="lever",
            ingested_at=now - timedelta(days=40),
        )
        repos.jobs.update(
            job.id,
            feed_state="expired",
            source_meta={"expired_at": (now - timedelta(days=1)).isoformat()},
        )
        repos.jobs.unexpire(job.id, now=now)
        job_id = job.id

    # Immediately after un-expire the 14-day timer restarts (feed_since = now),
    # so the same tick must NOT re-expire it despite the old ingested_at.
    result = age_expired_jobs(db, now=now)
    assert result["expired"] == []
    with db.repos() as repos:
        assert _job(repos, job_id).feed_state == "active"


def test_expire_window_is_configurable(migrated_db: Database) -> None:
    """The `expire_after_days` window is honoured — a 10-day-old job stays active
    at the default 14 but greys at a configured 7 (maintainer directive
    2026-08-01: the "Older listing" threshold is now user-owned)."""
    db = migrated_db
    now = now_utc()
    with db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="cfg", title="C", source_adapter="lever",
            ingested_at=now - timedelta(days=10),
        )
        job_id = job.id
    assert age_expired_jobs(db, now=now)["expired"] == []  # default 14 keeps it
    with db.repos() as repos:
        assert _job(repos, job_id).feed_state == "active"
    assert age_expired_jobs(db, now=now, expire_after_days=7)["expired"] == [job_id]


def test_daily_tick_uses_the_configured_expire_window(migrated_db: Database) -> None:
    """The maintenance tick reads `ui_state.lifecycle.expire_listing_days` and
    passes it to `age_expired_jobs`, so the Settings value actually governs when
    a listing greys to 'Older' (it used to call `age_expired_jobs` with the
    hard-coded default, ignoring the setting)."""
    from sidecar.app.registry.operations import OperationContext, cleanup_trash_entrypoint

    db = migrated_db
    now = now_utc()
    with db.repos() as repos:
        repos.preferences.update(ui_state={"lifecycle": {"expire_listing_days": 7}})
        job = repos.jobs.create(
            canonical_url="tick", title="T", source_adapter="lever",
            ingested_at=now - timedelta(days=10),
        )
        job_id = job.id
    outcome = cleanup_trash_entrypoint(
        OperationContext(kind="cleanup_trash", input_snapshot={}, db=db)
    )
    assert outcome.result_ref is not None
    assert job_id in outcome.result_ref["expired_ids"]  # 10 days > configured 7
    with db.repos() as repos:
        assert _job(repos, job_id).feed_state == "expired"


# ---------------------------------------------------------------------------
# work_style derivation (US-JB-01 chip / FR-JB-04 filter — one source)
# ---------------------------------------------------------------------------


def test_derive_work_style_keyword_cases() -> None:
    from sidecar.app.api.dto import derive_work_style

    # Remote signals (location or description).
    assert derive_work_style("Remote", "") == "REMOTE"
    assert derive_work_style("", "This is a fully remote role.") == "REMOTE"
    assert derive_work_style("Anywhere", "Work from home, WFH friendly.") == "REMOTE"
    # Hybrid outranks a stray remote mention (explicit constraint wins).
    assert derive_work_style("NYC", "Hybrid — 3 days in office, remote 2.") == "HYBRID"
    assert derive_work_style("Hybrid (London)", "") == "HYBRID"
    # Onsite signals.
    assert derive_work_style("Austin, TX", "On-site position, in-office team.") == "ONSITE"
    assert derive_work_style("", "This is an in person role.") == "ONSITE"
    # Undeterminable → empty (never guessed).
    assert derive_work_style("Berlin", "We build backend services in Go.") == ""
    assert derive_work_style("", "") == ""


def test_board_row_exposes_derived_work_style(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        repos.jobs.create(
            canonical_url="ws1", title="Remote Backend Engineer", source_adapter="lever",
            location="Remote", description="Fully remote team building services.",
        )
        repos.jobs.create(
            canonical_url="ws2", title="Onsite Analyst", source_adapter="lever",
            location="Chicago", description="On-site, in-office role.",
        )
    rows = {j["title"]: j for j in client.get("/api/board", headers=AUTH).json()["jobs"]}
    assert rows["Remote Backend Engineer"]["workStyle"] == "REMOTE"
    assert rows["Onsite Analyst"]["workStyle"] == "ONSITE"

def test_board_excludes_saved_jobs(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        job = repos.jobs.create(canonical_url="u1", title="A", source_adapter="lever")
        repos.applications.create(job.id, column="saved", priority="P0")
        job_id = job.id
    ids = [j["id"] for j in client.get("/api/board", headers=AUTH).json()["jobs"]]
    assert job_id not in ids  # Saved jobs leave the board (US-JB-06)


# ---------------------------------------------------------------------------
# Board search (FR-JB-13): list_q (title/company/location) + text_q (full text
# incl. JD + match-score reasons/breakdown), filtered before pagination.
# ---------------------------------------------------------------------------



def test_saved_job_never_auto_expires_or_deletes(migrated_db: Database) -> None:
    db = migrated_db
    now = now_utc()
    with db.repos() as repos:
        active = repos.jobs.create(
            canonical_url="a", title="A", source_adapter="lever",
            ingested_at=now - timedelta(days=40),
        )
        repos.applications.create(active.id, column="saved", priority="P0")
        expired = repos.jobs.create(canonical_url="e", title="E", source_adapter="lever")
        repos.jobs.update(
            expired.id,
            feed_state="expired",
            source_meta={"expired_at": (now - timedelta(days=40)).isoformat()},
        )
        repos.applications.create(expired.id, column="saved", priority="P0")
        active_id, expired_id = active.id, expired.id

    result = age_expired_jobs(db, now=now)
    assert result == {"expired": [], "deleted": []}  # Saving rescues both
    with db.repos() as repos:
        assert _job(repos, active_id).feed_state == "active"
        assert repos.jobs.get(expired_id) is not None


# ---------------------------------------------------------------------------
# Retroactive hard excludes (maintainer 2026-07-22): excludes hide
# already-discovered rows from the board — hidden, never deleted.
# ---------------------------------------------------------------------------


def test_hard_excludes_hide_existing_board_rows_and_restore(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    _seed_search_jobs(app)  # Stripe (payments JD) + Acme

    board = client.get("/api/board", headers=AUTH).json()
    assert board["total"] == 2

    # Adding a company exclude hides the Stripe row retroactively…
    client.post(
        "/api/settings",
        headers=AUTH,
        json={"hard_excludes": {"companies": ["Stripe"]}},
    )
    board = client.get("/api/board", headers=AUTH).json()
    assert board["total"] == 1
    assert board["jobs"][0]["company"] != "Stripe"
    # …but the row is hidden, not deleted: the DB still has it.
    with _db(app).repos() as repos:
        assert any(j.company == "Stripe" for j in repos.jobs.list())

    # A description keyword hides by JD content (word-boundary, like the scan).
    client.post(
        "/api/settings",
        headers=AUTH,
        json={"hard_excludes": {"companies": [], "keywords": ["payments"]}},
    )
    board = client.get("/api/board", headers=AUTH).json()
    assert board["total"] == 1
    assert board["jobs"][0]["company"] == "Acme"

    # Clearing the excludes brings everything straight back.
    client.post("/api/settings", headers=AUTH, json={"hard_excludes": {}})
    board = client.get("/api/board", headers=AUTH).json()
    assert board["total"] == 2


# ---------------------------------------------------------------------------
# "New in last scan" (maintainer 2026-07-23): the scan records the job ids it
# actually inserted in its result_ref; the board flags exactly those rows —
# from the LATEST succeeded scan only — as isNew. Manual adds and rows from
# older scans never carry the badge.
# ---------------------------------------------------------------------------


def test_persist_scan_records_new_job_ids(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    from sidecar.app.registry.persistence import persist_scan
    from sidecar.modules.scraper.types import NormalizedJob, ScanResult

    app, _client = app_client
    result = ScanResult(
        jobs=[
            NormalizedJob(
                title="X", canonical_url="https://ex.co/x", source_adapter="lever"
            )
        ]
    )
    ref = persist_scan(_db(app), result)
    with _db(app).repos() as repos:
        job = repos.jobs.get_by_canonical_url("https://ex.co/x")
        assert job is not None
        assert ref["scan"]["new_job_ids"] == [job.id]
    # Dedup: re-scanning the same URL inserts nothing and records nothing.
    ref2 = persist_scan(_db(app), result)
    assert ref2["scan"]["persisted"] == 0
    assert ref2["scan"]["new_job_ids"] == []


def test_board_flags_only_last_scans_jobs_as_new(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with _db(app).repos() as repos:
        old = repos.jobs.create(canonical_url="u-old", title="Old", source_adapter="lever")
        fresh = repos.jobs.create(canonical_url="u-new", title="Fresh", source_adapter="lever")
        manual = repos.jobs.create(
            canonical_url="u-manual", title="Manual", source_adapter="paste-url"
        )
        op1 = repos.operations.create("scan", {})
        repos.operations.mark_succeeded(
            op1.id, result_ref={"scan": {"persisted": 1, "new_job_ids": [old.id]}}
        )
        op2 = repos.operations.create("scan", {})
        repos.operations.mark_succeeded(
            op2.id, result_ref={"scan": {"persisted": 1, "new_job_ids": [fresh.id]}}
        )
        old_id, fresh_id, manual_id = old.id, fresh.id, manual.id

    rows = {j["id"]: j for j in client.get("/api/board", headers=AUTH).json()["jobs"]}
    assert rows[fresh_id]["isNew"] is True  # last succeeded scan's insert
    assert rows[old_id]["isNew"] is False  # an older scan's insert
    assert rows[manual_id]["isNew"] is False  # manual add — never badged


def test_board_is_new_absent_result_ref_is_graceful(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Scans recorded before new_job_ids existed (or a scan with no inserts)
    simply produce no badges — never an error."""
    app, client = app_client
    with _db(app).repos() as repos:
        job = repos.jobs.create(canonical_url="u1", title="A", source_adapter="lever")
        op = repos.operations.create("scan", {})
        repos.operations.mark_succeeded(op.id, result_ref={"scan": {"persisted": 0}})
        job_id = job.id
    rows = {j["id"]: j for j in client.get("/api/board", headers=AUTH).json()["jobs"]}
    assert rows[job_id]["isNew"] is False
