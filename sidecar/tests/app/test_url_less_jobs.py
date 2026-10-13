"""Covers: a job with no posting URL, end to end (url-less-jobs.md).

Identity: post the same JD twice, get 1 row. A URL row keys on its URL, a
URL-less row on its description hash, and the 2 never collide; dedup, tombstone
lookup and Trash restore read whichever key a row has; `age_expired_jobs` skips
rows the feed can't replace (section 5.6)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.db.base import now_utc
from sidecar.app.registry.persistence import age_expired_jobs
from sidecar.modules.scraper import ScraperError
from sidecar.modules.scraper.identity import content_hash
from sidecar.modules.scraper.types import NormalizedJob

from ._support import AUTH

_JD = (
    "We are hiring a Senior Software Engineer to own our ingestion pipeline. "
    "You will design services in Python, run them on AWS, and mentor two "
    "junior engineers. Five years of backend experience required."
)


def _paste(client: TestClient, description: str, **over: str) -> dict:
    body: dict[str, str] = {
        "title": "Senior Software Engineer",
        "company": "Acme",
        "description": description,
        "source_adapter": "paste-text",
    }
    body.update(over)
    return client.post("/api/jobs", headers=AUTH, json=body).json()


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_a_job_with_no_link_is_created_and_says_so(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/jobs",
        headers=AUTH,
        json={
            "title": "Senior Software Engineer",
            "company": "Acme",
            "description": _JD,
            "source_adapter": "paste-text",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["canonical_url"] is None


def test_the_same_jd_pasted_twice_is_one_row(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    # The acceptance test for Layer 1. A per-paste UUID identity passes the
    # unique index and still writes 2 rows (section 6.3).
    _app, client = app_client
    first = _paste(client, _JD)
    second = _paste(client, _JD)
    assert first["id"] == second["id"]

    board = client.get("/api/board", headers=AUTH).json()
    assert [j["id"] for j in board["jobs"]] == [first["id"]]


def test_whitespace_only_differences_are_the_same_job(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    # 2 of 3 OneTrust requisitions in the bench corpus differ by 1 non-breaking
    # space in 7,437 characters. Raw hashing calls those 2 jobs (section 6.2).
    _app, client = app_client
    first = _paste(client, _JD)
    second = _paste(client, _JD.replace(" ", " \n", 1).upper())
    assert first["id"] == second["id"]


def test_different_jds_are_different_jobs(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    first = _paste(client, _JD)
    second = _paste(client, _JD + " Kubernetes experience is a plus.")
    assert first["id"] != second["id"]


def test_a_job_needs_a_link_or_a_description(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/jobs", headers=AUTH, json={"title": "Senior Software Engineer"}
    )
    assert resp.status_code == 422


def test_prose_in_the_link_box_never_becomes_an_identity(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    # The hand-fill hatch used to hand a failed fetch's typed string straight
    # to canonical_url, which is how an English sentence became a job's
    # identity (section 3.4).
    _app, client = app_client
    created = _paste(client, _JD, canonical_url="Senior Engineer at Acme, via email")
    assert created["canonical_url"] is None


def test_url_jobs_sharing_a_description_do_not_collide(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    # 5 Tekion requisitions in the corpus share one byte-identical
    # description. Hashing a row that already has a URL would make the 2nd
    # one violate the unique hash index (section 8.2).
    _app, client = app_client
    one = _paste(
        client, _JD, canonical_url="https://boards.greenhouse.io/acme/jobs/1"
    )
    two = _paste(
        client, _JD, canonical_url="https://boards.greenhouse.io/acme/jobs/2"
    )
    assert one["id"] != two["id"]


# ---------------------------------------------------------------------------
# tombstones and Trash
# ---------------------------------------------------------------------------


def test_delete_forever_never_tombstones_a_typed_in_job(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """A tombstone stops a scan re-adding a posting. No scan can produce a job
    the user typed in, so deleting one forever only deletes it, and the same
    description can be pasted again later (maintainer, 2026-10-10)."""
    app, client = app_client
    created = _paste(client, _JD)
    client.patch(
        f"/api/jobs/{created['id']}", headers=AUTH, json={"feed_state": "removed"}
    )
    result = client.post(f"/api/jobs/{created['id']}/tombstone", headers=AUTH)
    assert result.status_code == 200
    assert result.json() == {"tombstoned": 0, "canonical_urls": []}

    again = client.post(
        "/api/jobs",
        headers=AUTH,
        json={"title": "Senior Software Engineer", "description": _JD},
    )
    assert again.status_code == 201
    assert again.json()["id"] != created["id"]
    with app.state.db.repos() as repos:
        assert repos.jobs.get(created["id"]) is None


def test_a_scan_is_never_blocked_by_a_deleted_typed_in_job(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """The real posting for a JD the user once pasted and deleted still lands."""
    from sidecar.app.registry.persistence import persist_scan
    from sidecar.modules.scraper.types import ScanResult

    app, client = app_client
    created = _paste(client, _JD)
    client.patch(
        f"/api/jobs/{created['id']}", headers=AUTH, json={"feed_state": "removed"}
    )
    client.post(f"/api/jobs/{created['id']}/tombstone", headers=AUTH)
    scanned = NormalizedJob(
        canonical_url=_URL, title="Senior Software Engineer", company="Acme",
        location="", description=_JD, source_adapter="greenhouse",
    )
    ref = persist_scan(app.state.db, ScanResult(jobs=[scanned], per_source={}))
    assert ref["scan"]["persisted"] == 1
    assert ref["scan"]["tombstoned"] == 0


def test_delete_forever_on_a_scanned_job_tombstones_its_url(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    from sidecar.app.registry.persistence import persist_scan
    from sidecar.modules.scraper.types import ScanResult

    app, client = app_client
    scanned = NormalizedJob(
        canonical_url=_URL, title="Senior Software Engineer", company="Acme",
        location="", description=_JD, source_adapter="greenhouse",
    )
    persist_scan(app.state.db, ScanResult(jobs=[scanned], per_source={}))
    with app.state.db.repos() as repos:
        job = repos.jobs.get_by_canonical_url(_URL)
        assert job is not None and job.origin == "scan"
        job_id = job.id
    client.patch(f"/api/jobs/{job_id}", headers=AUTH, json={"feed_state": "removed"})
    result = client.post(f"/api/jobs/{job_id}/tombstone", headers=AUTH)
    assert result.json() == {"tombstoned": 1, "canonical_urls": [_URL]}
    with app.state.db.repos() as repos:
        assert repos.tombstones.exists(_URL)


def test_delete_forever_on_a_hand_added_link_is_only_a_delete(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Only a scanned job is tombstoned (maintainer, 2026-10-12), including a
    hand-added link that autofilled from a board and so carries its name."""
    app, client = app_client
    created = _paste(client, _JD, canonical_url=_URL, source_adapter="greenhouse")
    client.patch(
        f"/api/jobs/{created['id']}", headers=AUTH, json={"feed_state": "removed"}
    )
    result = client.post(f"/api/jobs/{created['id']}/tombstone", headers=AUTH)
    assert result.json() == {"tombstoned": 0, "canonical_urls": []}
    with app.state.db.repos() as repos:
        assert not repos.tombstones.exists(_URL)
        assert repos.jobs.get(created["id"]) is None


def test_a_trashed_paste_comes_back_on_a_re_paste(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    created = _paste(client, _JD)
    client.patch(
        f"/api/jobs/{created['id']}", headers=AUTH, json={"feed_state": "removed"}
    )
    restored = _paste(client, _JD)
    assert restored["id"] == created["id"]
    assert restored["feed_state"] == "active"


# ---------------------------------------------------------------------------
# the lifecycle sweep (section 5.6)
# ---------------------------------------------------------------------------


def test_a_hand_added_job_never_ages_out_link_or_not(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    typed = _paste(client, _JD)
    linked = _paste(
        client,
        _JD + " Pasted with its link, from a board no scan reads.",
        canonical_url="https://careers.smallco.example/jobs/42",
    )
    with app.state.db.repos() as repos:
        scanned = repos.jobs.create(
            canonical_url="https://boards.greenhouse.io/acme/jobs/7", title="Eng",
            company="Acme", description=_JD + " A scanned row.", source_adapter="greenhouse",
        )
        scanned_id = scanned.id
    old = now_utc() - timedelta(days=400)
    with app.state.db.repos() as repos:
        for job_id in (typed["id"], linked["id"], scanned_id):
            repos.jobs.update(job_id, feed_since=old, ingested_at=old)

    result = age_expired_jobs(app.state.db)
    assert result["expired"] == [scanned_id]

    with app.state.db.repos() as repos:
        for job_id in (typed["id"], linked["id"], scanned_id):
            repos.jobs.update(job_id, expired_at=old)
    result = age_expired_jobs(app.state.db)
    assert result["deleted"] == [scanned_id]

    with app.state.db.repos() as repos:
        for job_id in (typed["id"], linked["id"]):
            assert repos.jobs.get(job_id).feed_state == "active"


def test_a_job_given_its_link_later_stays_hand_added(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client
    typed = _paste(client, _JD)
    monkeypatch.setattr("sidecar.app.api.jobs.probe_url", _probe(_JD))
    resp = client.post(f"/api/jobs/{typed['id']}/link", headers=AUTH, json={"url": _URL})
    assert resp.status_code == 200, resp.text
    old = now_utc() - timedelta(days=400)
    with app.state.db.repos() as repos:
        repos.jobs.update(typed["id"], feed_since=old, ingested_at=old)
    assert age_expired_jobs(app.state.db)["expired"] == []
    with app.state.db.repos() as repos:
        job = repos.jobs.get(typed["id"])
        assert job.canonical_url and job.origin == "manual" and job.feed_state == "active"


# ---------------------------------------------------------------------------
# the board's link filter (section 9.3)
# ---------------------------------------------------------------------------


def test_the_link_filter_splits_the_board_on_the_url_axis(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    typed = _paste(client, _JD)
    linked = _paste(
        client,
        _JD + " Posted on our board.",
        canonical_url="https://boards.greenhouse.io/acme/jobs/9",
    )

    has = client.get("/api/board", headers=AUTH, params={"link": "has"}).json()
    assert [j["id"] for j in has["jobs"]] == [linked["id"]]

    none = client.get("/api/board", headers=AUTH, params={"link": "none"}).json()
    assert [j["id"] for j in none["jobs"]] == [typed["id"]]

    both = client.get("/api/board", headers=AUTH).json()
    assert both["total"] == 2


# ---------------------------------------------------------------------------
# promotion (sections 5.4 and 5.5)
# ---------------------------------------------------------------------------

_URL = "https://boards.greenhouse.io/acme/jobs/9"


def _probe(description: str, title: str = "Senior Software Engineer", company: str = "Acme"):
    def _fake(_url: str, *_a: object, **_k: object) -> NormalizedJob:
        return NormalizedJob(
            canonical_url=_URL,
            title=title,
            company=company,
            location="Pune, India",
            description=description,
            source_adapter="greenhouse",
        )

    return _fake


def test_an_exact_text_match_attaches_silently(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client
    job = _paste(client, _JD)
    monkeypatch.setattr("sidecar.app.api.jobs.probe_url", _probe(_JD))

    resp = client.post(f"/api/jobs/{job['id']}/link", headers=AUTH, json={"url": _URL})
    assert resp.status_code == 200
    assert resp.json()["status"] == "attached"
    assert resp.json()["job"]["canonical_url"] == _URL

    with app.state.db.repos() as repos:
        row = repos.jobs.get(job["id"])
        # The old hash stays, so a later re-paste of the same text finds this
        # row instead of writing a second one (section 5.4).
        assert row.content_hash == content_hash(_JD)
    assert _paste(client, _JD)["id"] == job["id"]


def test_promotion_keeps_the_application_and_its_documents(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = app_client
    job = _paste(client, _JD)
    saved = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job["id"], "generate_resume": False, "generate_cover": False},
    ).json()
    monkeypatch.setattr("sidecar.app.api.jobs.probe_url", _probe(_JD))

    client.post(f"/api/jobs/{job['id']}/link", headers=AUTH, json={"url": _URL})

    after = client.get(f"/api/applications/{saved['id']}", headers=AUTH).json()
    assert after["job"]["canonical_url"] == _URL


def test_adjacent_seniority_comes_back_as_a_mismatch(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The failure a bare 90% similarity gate accepts (section 6.4): same
    # company, same boilerplate, a different seniority.
    _app, client = app_client
    job = _paste(client, _JD)
    monkeypatch.setattr(
        "sidecar.app.api.jobs.probe_url",
        _probe(_JD + " Equity included.", title="Principal Software Engineer"),
    )

    body = client.post(
        f"/api/jobs/{job['id']}/link", headers=AUTH, json={"url": _URL}
    ).json()
    assert body["status"] == "mismatch"
    assert body["pasted"]["title"] == "Principal Software Engineer"
    assert body["card"]["title"] == "Senior Software Engineer"
    assert body["job"] is None  # nothing written


def test_confirm_attaches_a_mismatch_anyway(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rank-don't-gate: confirming is always available, it just isn't default.
    _app, client = app_client
    job = _paste(client, _JD)
    monkeypatch.setattr(
        "sidecar.app.api.jobs.probe_url",
        _probe("Something else entirely.", title="Product Designer"),
    )

    assert (
        client.post(f"/api/jobs/{job['id']}/link", headers=AUTH, json={"url": _URL})
        .json()["status"]
        == "mismatch"
    )
    body = client.post(
        f"/api/jobs/{job['id']}/link",
        headers=AUTH,
        json={"url": _URL, "confirm": True},
    ).json()
    assert body["status"] == "attached"
    assert body["job"]["canonical_url"] == _URL


def test_an_unreadable_page_says_so_instead_of_refusing(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = app_client
    job = _paste(client, _JD)

    def _boom(_url: str, *_a: object, **_k: object) -> NormalizedJob:
        raise ScraperError("fetch", "likely a JS-only page")

    monkeypatch.setattr("sidecar.app.api.jobs.probe_url", _boom)
    body = client.post(
        f"/api/jobs/{job['id']}/link", headers=AUTH, json={"url": _URL}
    ).json()
    assert body["status"] == "unreadable"
    assert body["job"] is None


def test_a_link_another_job_already_has_is_reported_and_writes_nothing(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flow D: pasted Monday, scanned Wednesday, linked Friday. The dialog names
    the other job and changes neither row; the user deletes the one they don't
    need (maintainer, url-less-jobs.md section 5.5). Both rows tracked or
    not, it's the same answer, so there is no refusal case and no 500."""
    app, client = app_client
    mine = _paste(client, _JD)
    scanned = _paste(
        client, _JD + " Board copy.", canonical_url=_URL, source_adapter="greenhouse"
    )
    client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": scanned["id"], "generate_resume": False, "generate_cover": False},
    )
    monkeypatch.setattr("sidecar.app.api.jobs.probe_url", _probe(_JD))

    for confirm in (False, True):
        body = client.post(
            f"/api/jobs/{mine['id']}/link",
            headers=AUTH,
            json={"url": _URL, "confirm": confirm},
        ).json()
        assert body["status"] == "duplicate"
        assert body["other_job_id"] == scanned["id"]
    with app.state.db.repos() as repos:
        kept_mine, kept_scanned = repos.jobs.get(mine["id"]), repos.jobs.get(scanned["id"])
        assert kept_mine is not None and kept_mine.canonical_url is None
        assert kept_scanned is not None and kept_scanned.canonical_url == _URL


def test_apply_refuses_a_url_less_card_before_writing_a_run(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    # Section 9.8: the refusal lands before `apply_runs.create`, so the panel
    # never shows a run that had no chance of working.
    app, client = app_client
    job = _paste(client, _JD)
    saved = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job["id"], "generate_resume": False, "generate_cover": False},
    ).json()

    resp = client.post(f"/api/applications/{saved['id']}/apply", headers=AUTH)
    assert resp.status_code == 409
    assert "no posting link" in resp.json()["detail"]
    with app.state.db.repos() as repos:
        assert repos.apply_runs.latest_for_application(saved["id"]) is None

    # And an EMPTY dev form url is not a form either, so the dev seam below
    # cannot be used to slip past the refusal by accident.
    resp = client.post(
        f"/api/applications/{saved['id']}/apply",
        headers=AUTH,
        json={"dev": {"form_url": ""}},
    )
    assert resp.status_code == 409


def test_a_dev_run_may_carry_its_own_form_because_a_fixture_is_not_a_posting(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """A fixture form is a file:// URL, which `canonicalize_url` rejects, so the
    no-link guard took the Applier's end-to-end test with it. The fixture rides the
    dev knobs instead; production still gets its 409."""
    app, client = app_client
    job = _paste(client, _JD)
    saved = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job["id"], "generate_resume": False, "generate_cover": False},
    ).json()

    resp = client.post(
        f"/api/applications/{saved['id']}/apply",
        headers=AUTH,
        json={"dev": {"form_url": "file:///tmp/fixture-form.html", "allow_local": True}},
    )
    assert resp.status_code in (200, 201, 202), resp.text
    with app.state.db.repos() as repos:
        run = repos.apply_runs.latest_for_application(saved["id"])
        assert run is not None
        # The run records the form it will actually open, not an empty string.
        assert run.source_url == "file:///tmp/fixture-form.html"


def test_the_tracker_logs_an_application_with_no_link(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    # Section 9.2: the person logging an application they made 3 weeks ago out
    # of an email is the person least likely to still have the URL.
    _app, client = app_client
    resp = client.post(
        "/api/applications/manual",
        headers=AUTH,
        data={
            "canonical_url": "",
            "title": "Logged Engineer",
            "company": "Umbrella",
            "description": _JD,
            "source_adapter": "paste-text",
            "column": "applied",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["job"]["canonical_url"] is None
