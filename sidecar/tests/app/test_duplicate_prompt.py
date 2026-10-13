"""The duplicate prompt, and where "they're different" lives (D15).

A pasted JD and a scan's row for the same posting are both correct by their own
key, so the answer is to ask once. Pinned: the flag is written at ingest; the
gate is compound (Senior and Principal differ); a dismissal belongs to the pair,
holds from either card, and goes with either job."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.main import create_app
from sidecar.app.registry.persistence import delete_job_cascade

TOKEN = "test-token-duplicate-prompt"  # noqa: S105 — test fixture
AUTH = {"Authorization": f"Bearer {TOKEN}"}

JD = (
    "Senior Software Engineer at Acme. You will own distributed Python services, "
    "design PostgreSQL schemas, run workloads on AWS and mentor other engineers. "
    "Requirements: 6+ years of backend experience, strong Python, solid SQL, and "
    "production ownership. Nice to have: Go, Kubernetes, and event-driven design."
)


def _client(tmp_path: Any) -> tuple[TestClient, Any]:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    return TestClient(app), app


def _add(client: TestClient, *, url: str, title: str, company: str, jd: str = JD) -> str:
    resp = client.post(
        "/api/jobs",
        headers=AUTH,
        json={
            "canonical_url": url,
            "title": title,
            "company": company,
            "location": "Pune, India",
            "description": jd,
            "source_adapter": "greenhouse",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def test_the_second_row_is_flagged_when_it_is_written(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        monday = _add(client, url="", title="Senior Software Engineer", company="Acme")
        wednesday = _add(
            client,
            url="https://boards.greenhouse.io/acme/jobs/1",
            title="Senior Software Engineer",
            company="Acme",
        )
        # The pair is recorded at write, before anything renders.
        with app.state.db.repos() as repos:
            found = repos.duplicates.open_for(wednesday)
            assert found is not None and found[0] == monday

        body = client.get(f"/api/jobs/{wednesday}/duplicate", headers=AUTH).json()
        assert body is not None
        assert body["other_job_id"] == monday
        assert body["other"]["title"] == "Senior Software Engineer"


def test_a_different_level_at_the_same_employer_is_not_a_duplicate(tmp_path) -> None:
    """S-C69: bare similarity called uipath's Principal, Senior and SWE2 the
    same posting. The compound gate is why this test can exist."""
    client, _ = _client(tmp_path)
    with client:
        _add(client, url="https://ex.co/a", title="Senior Software Engineer", company="Acme")
        principal = _add(
            client, url="https://ex.co/b", title="Principal Software Engineer", company="Acme"
        )
        assert client.get(f"/api/jobs/{principal}/duplicate", headers=AUTH).json() is None


def test_a_different_employer_is_not_a_duplicate_either(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _add(client, url="https://ex.co/a", title="Senior Software Engineer", company="Acme")
        other = _add(
            client, url="https://ex.co/b", title="Senior Software Engineer", company="Globex"
        )
        assert client.get(f"/api/jobs/{other}/duplicate", headers=AUTH).json() is None


def test_a_dismissal_holds_from_either_card_and_asking_twice_is_idempotent(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        monday = _add(client, url="", title="Senior Software Engineer", company="Acme")
        wednesday = _add(
            client, url="https://ex.co/w", title="Senior Software Engineer", company="Acme"
        )
        assert client.get(f"/api/jobs/{wednesday}/duplicate", headers=AUTH).json() is not None

        resp = client.post(
            f"/api/jobs/{wednesday}/duplicate/dismiss",
            headers=AUTH,
            json={"other_job_id": monday},
        )
        assert resp.status_code == 200
        # Gone from the card it was dismissed on...
        assert client.get(f"/api/jobs/{wednesday}/duplicate", headers=AUTH).json() is None
        # ...and from the other one, because the fact is about the pair.
        assert client.get(f"/api/jobs/{monday}/duplicate", headers=AUTH).json() is None

        # Asking again changes nothing, and writes no second row.
        client.post(
            f"/api/jobs/{monday}/duplicate/dismiss",
            headers=AUTH,
            json={"other_job_id": wednesday},
        )
        with app.state.db.repos() as repos:
            assert repos.duplicates.open_summary([monday, wednesday]) == ({}, set())


def test_deleting_either_job_takes_the_dismissal_with_it(tmp_path) -> None:
    """The reason this is a table with 2 real foreign keys rather than an id
    inside a JSON column: a blob has nothing to cascade, so the id would outlive
    the row and wait to fire against whatever later takes its place."""
    client, app = _client(tmp_path)
    with client:
        monday = _add(client, url="", title="Senior Software Engineer", company="Acme")
        wednesday = _add(
            client, url="https://ex.co/w", title="Senior Software Engineer", company="Acme"
        )
        client.post(
            f"/api/jobs/{wednesday}/duplicate/dismiss",
            headers=AUTH,
            json={"other_job_id": monday},
        )
        db: Database = app.state.db
        with db.repos() as repos:
            delete_job_cascade(repos, wednesday)
        with db.repos() as repos:
            from sqlalchemy import select

            from sidecar.app.db.models import JobDuplicate

            assert repos.duplicates._s.scalars(select(JobDuplicate)).first() is None  # noqa: SLF001


def test_there_is_no_merge_the_user_resolves_a_duplicate_themselves(tmp_path) -> None:
    """The app says 2 rows look like 1 job and stops there (maintainer,
    url-less-jobs.md section 5.5). Nothing deletes or re-keys either row."""
    client, app = _client(tmp_path)
    with client:
        monday = _add(client, url="", title="Senior Software Engineer", company="Acme")
        wednesday = _add(
            client, url="https://ex.co/w", title="Senior Software Engineer", company="Acme"
        )
        resp = client.post(
            f"/api/jobs/{monday}/duplicate/merge",
            headers=AUTH,
            json={"other_job_id": wednesday},
        )
        assert resp.status_code in (404, 405)
        with app.state.db.repos() as repos:
            assert repos.jobs.get(monday) is not None
            assert repos.jobs.get(wednesday) is not None


def test_a_scan_flags_the_job_the_user_pasted_on_monday(tmp_path) -> None:
    """Flow D through the real scan path. The flag used to be written only by
    `POST /api/jobs`, so a scanned row was never compared with anything and
    this test's predecessor faked the scan with a second manual add."""
    from sidecar.app.registry.persistence import persist_scan
    from sidecar.modules.scraper.types import NormalizedJob, ScanResult

    client, app = _client(tmp_path)
    with client:
        monday = _add(client, url="", title="Senior Software Engineer", company="Acme")
        scanned = NormalizedJob(
            canonical_url="https://boards.greenhouse.io/acme/jobs/1",
            title="Senior Software Engineer", company="Acme", location="",
            description=JD + " Apply today.", source_adapter="greenhouse",
        )
        ref = persist_scan(app.state.db, ScanResult(jobs=[scanned], per_source={}))
        wednesday = ref["scan"]["new_job_ids"][0]
        body = client.get(f"/api/jobs/{wednesday}/duplicate", headers=AUTH).json()
        assert body is not None
        assert body["other_job_id"] == monday


def test_a_row_written_before_fingerprints_existed_is_backfilled(tmp_path) -> None:
    """Rows from before migration c9e4a71b3f02 had no fingerprint, and the
    duplicate check skips a NULL, so they could never be matched. The daily
    maintenance pass fills them in, once."""
    from sidecar.app.registry.persistence import backfill_fingerprints

    client, app = _client(tmp_path)
    with client:
        db: Database = app.state.db
        with db.repos() as repos:
            old = repos.jobs.create(
                canonical_url="https://ex.co/old", title="Senior Software Engineer",
                company="Acme", description=JD, source_adapter="greenhouse",
            ).id
        assert backfill_fingerprints(db) == 1
        assert backfill_fingerprints(db) == 0  # converged
        pasted = _add(client, url="", title="Senior Software Engineer", company="Acme")
        body = client.get(f"/api/jobs/{pasted}/duplicate", headers=AUTH).json()
        assert body is not None and body["other_job_id"] == old


def test_the_backfill_skips_an_expired_job(tmp_path) -> None:
    """The duplicate check compares active jobs only, so an expired job's
    fingerprint would never be read. It gets one if it's restored."""
    from sidecar.app.registry.persistence import backfill_fingerprints

    client, app = _client(tmp_path)
    with client:
        db: Database = app.state.db
        with db.repos() as repos:
            old = repos.jobs.create(
                canonical_url="https://ex.co/expired", title="Senior Software Engineer",
                company="Acme", description=JD, source_adapter="greenhouse",
            ).id
            repos.jobs.update(old, feed_state="expired")
        assert backfill_fingerprints(db) == 0
        with db.repos() as repos:
            repos.jobs.update(old, feed_state="active")
        assert backfill_fingerprints(db) == 1


def _board(client: TestClient) -> dict[str, Any]:
    return {j["id"]: j for j in client.get("/api/board", headers=AUTH).json()["jobs"]}


def test_the_card_stops_saying_reposted_once_the_pair_is_dismissed(tmp_path) -> None:
    """The chip and the notice read the same row, so "They're different" clears
    both. When the chip read a JSON flag the dismissal never touched, the card
    kept warning about a pair the user had just called 2 jobs."""
    client, _ = _client(tmp_path)
    with client:
        monday = _add(client, url="", title="Senior Software Engineer", company="Acme")
        wednesday = _add(
            client, url="https://ex.co/w", title="Senior Software Engineer", company="Acme"
        )
        board = _board(client)
        assert board[wednesday]["duplicates"] == 1
        assert board[wednesday]["duplicateNotice"] and board[monday]["duplicateNotice"]
        assert board[monday]["duplicates"] == 0  # the original isn't the repost

        client.post(
            f"/api/jobs/{wednesday}/duplicate/dismiss", headers=AUTH, json={"other_job_id": monday}
        )
        board = _board(client)
        assert board[wednesday]["duplicates"] == 0
        assert not board[wednesday]["duplicateNotice"] and not board[monday]["duplicateNotice"]


def test_the_notice_shows_from_both_sides(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        monday = _add(client, url="", title="Senior Software Engineer", company="Acme")
        wednesday = _add(
            client, url="https://ex.co/w", title="Senior Software Engineer", company="Acme"
        )
        body = client.get(f"/api/jobs/{monday}/duplicate", headers=AUTH).json()
        assert body is not None and body["other_job_id"] == wednesday


def test_a_third_copy_is_noticed_after_the_second_is_deleted(tmp_path) -> None:
    """The JSON flag was written once and never again, so once copy 2 was
    deleted, copy 3 of the same posting went unnoticed."""
    client, app = _client(tmp_path)
    with client:
        first = _add(client, url="", title="Senior Software Engineer", company="Acme")
        second = _add(
            client, url="https://ex.co/2", title="Senior Software Engineer", company="Acme"
        )
        with app.state.db.repos() as repos:
            delete_job_cascade(repos, second)
        third = _add(
            client, url="https://ex.co/3", title="Senior Software Engineer", company="Acme"
        )
        body = client.get(f"/api/jobs/{third}/duplicate", headers=AUTH).json()
        assert body is not None and body["other_job_id"] == first
