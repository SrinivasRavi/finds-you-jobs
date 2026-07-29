"""Covers: the applications vertical HTTP surface (2026-07-09 audit fixes).

Through the real app (TestClient → lifespan → real migration + runner):

- artifact PATCH (US-RES-02 / FR-RES-02): persist edited markdown + the
  Approve-and-Save flip; per-artifact `packetResumeState` / `packetCoverLetterState`.
- priority assignment (FR-TR-09): Welford z-band at Save, P0 when saved Pending,
  explicit override wins.
- split auto-generate-on-Save defaults (FR-SET-02): Resume ON / Cover ON.

No live LLM: artifacts are seeded directly (operation_id=None → `ready`), so the
route logic — not a routed engine — is what's under test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.priority import STATS_KEY

TOKEN = "test-token-appvert"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


def _seed_job(app: FastAPI, *, url: str = "https://ex.co/j/av", score: int | None = None) -> str:
    db = app.state.db
    with db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer.")
        job = repos.jobs.create(
            canonical_url=url, title="Backend Engineer", company="Glean",
            source_adapter="greenhouse",
        )
        if score is not None:
            version = repos.profile.get_current().version
            repos.job_scores.upsert(
                job_id=job.id, profile_version=version, score_0_100=score,
            )
        return job.id


def _seed_ready_packet(app: FastAPI, job_id: str) -> str:
    """A Saved application with a ready resume + cover (operation_id=None)."""
    db = app.state.db
    with db.repos() as repos:
        app_row = repos.applications.create(job_id, column="saved", priority="P2")
        for kind in ("tailored_resume", "cover_letter"):
            repos.artifacts.create(
                app_row.id, kind=kind, markdown=f"# {kind}", notes=[], profile_version=1,
            )
        return app_row.id


# ---------------------------------------------------------------------------
# artifact PATCH + split states (US-RES-02 / FR-RES-02)
# ---------------------------------------------------------------------------


def test_patch_artifact_saves_markdown_and_approves_only_that_slot(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    job_id = _seed_job(app)
    app_id = _seed_ready_packet(app, job_id)

    before = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert before["packetResumeState"] == "ready"
    assert before["packetCoverLetterState"] == "ready"

    resp = client.patch(
        f"/api/applications/{app_id}/artifacts/tailored_resume",
        headers=AUTH, json={"markdown": "# Edited variant", "approved": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["packetResumeState"] == "approved"   # only the resume flipped
    assert body["packetCoverLetterState"] == "ready"  # cover untouched
    resume = next(a for a in body["artifacts"] if a["kind"] == "tailored_resume")
    assert resume["markdown"] == "# Edited variant"
    assert resume["approved_at"] is not None


def test_patch_artifact_unapprove_reverts_to_ready(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    client.patch(
        f"/api/applications/{app_id}/artifacts/cover_letter",
        headers=AUTH, json={"approved": True},
    )
    resp = client.patch(
        f"/api/applications/{app_id}/artifacts/cover_letter",
        headers=AUTH, json={"approved": False},
    )
    assert resp.json()["packetCoverLetterState"] == "ready"


def test_patch_artifact_unknown_kind_400(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    resp = client.patch(
        f"/api/applications/{app_id}/artifacts/master", headers=AUTH, json={"approved": True}
    )
    assert resp.status_code == 400


def test_patch_artifact_creates_manual_variant_when_missing(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Paste-your-own variant saves even when nothing was generated
    (create-if-missing): a PATCH carrying markdown on a missing artifact creates a
    manual artifact (no operation) that reads `ready`; an approve-only PATCH with
    no text on a missing artifact still 404s."""
    app, client = app_client
    job_id = _seed_job(app)
    db = app.state.db
    with db.repos() as repos:
        app_id = repos.applications.create(job_id, column="saved").id  # no artifacts

    # Markdown on a missing artifact creates it (the paste-your-own path).
    resp = client.patch(
        f"/api/applications/{app_id}/artifacts/tailored_resume",
        headers=AUTH,
        json={"markdown": "# My own tailored resume"},
    )
    assert resp.status_code == 200
    assert resp.json()["packetResumeState"] == "ready"
    with db.repos() as repos:
        head = next(
            a
            for a in repos.artifacts.list_for_application(app_id)
            if a.kind == "tailored_resume" and a.superseded_by is None
        )
        assert head.markdown == "# My own tailored resume"
        assert head.operation_id is None

    # An approve-only flip with no text has nothing to create → still 404.
    resp2 = client.patch(
        f"/api/applications/{app_id}/artifacts/cover_letter",
        headers=AUTH,
        json={"approved": True},
    )
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# priority assignment at Save (FR-TR-09)
# ---------------------------------------------------------------------------


def _prime_distribution(app: FastAPI, mean: float = 60.0) -> None:
    """Seed a warm Welford accumulator (count ≥ 20) centered at `mean`, σ≈8."""
    db = app.state.db
    from sidecar.app.priority import welford_update

    stats = {"count": 0, "mean": 0.0, "m2": 0.0}
    for i in range(40):
        stats = welford_update(stats, mean + 8.0 * ((i % 20) - 10) / 6.0)
    with db.repos() as repos:
        prefs = repos.preferences.get_or_create()
        thresholds = dict(prefs.thresholds or {})
        thresholds[STATS_KEY] = stats
        repos.preferences.update(thresholds=thresholds)


def test_priority_zband_assigned_at_save_for_scored_job(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    _prime_distribution(app, mean=60.0)
    job_id = _seed_job(app, score=95)  # well above μ → P0
    resp = client.post("/api/applications", headers=AUTH, json={"job_id": job_id})
    assert resp.status_code == 201
    assert resp.json()["priority"] == "P0"


def test_priority_p0_when_saved_while_pending(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    _prime_distribution(app)
    job_id = _seed_job(app, score=None)  # no cached score → Pending
    resp = client.post("/api/applications", headers=AUTH, json={"job_id": job_id})
    assert resp.json()["priority"] == "P0"  # skips the z-band


def test_priority_cold_start_defaults_to_p2(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client  # no primed distribution → cold start
    job_id = _seed_job(app, score=88)
    resp = client.post("/api/applications", headers=AUTH, json={"job_id": job_id})
    assert resp.json()["priority"] == "P2"


def test_priority_explicit_override_wins(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    _prime_distribution(app, mean=60.0)
    job_id = _seed_job(app, score=95)  # would be P0 by z-band
    resp = client.post(
        "/api/applications", headers=AUTH, json={"job_id": job_id, "priority": "P3"}
    )
    assert resp.json()["priority"] == "P3"  # manual choice is used verbatim


# ---------------------------------------------------------------------------
# split auto-generate-on-Save defaults (FR-SET-02)
# ---------------------------------------------------------------------------


def _artifact_kinds(client: TestClient, app_id: str) -> set[str]:
    body = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    return {a["kind"] for a in body["artifacts"]}


def test_auto_generate_defaults_on_for_both_resume_and_cover(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client  # no thresholds set → both default ON (FR-SET-02)
    job_id = _seed_job(app)
    app_id = client.post("/api/applications", headers=AUTH, json={"job_id": job_id}).json()["id"]
    assert _artifact_kinds(client, app_id) == {"tailored_resume", "cover_letter"}


def test_per_job_toggles_override_defaults(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    job_id = _seed_job(app)
    app_id = client.post(
        "/api/applications", headers=AUTH,
        json={"job_id": job_id, "generate_resume": True, "generate_cover": False},
    ).json()["id"]
    assert _artifact_kinds(client, app_id) == {"tailored_resume"}


# ---------------------------------------------------------------------------
# Activity events: column move / notes edit / archive (FR-TR-03 / FR-TR-04)
# ---------------------------------------------------------------------------


def _activity_kinds(client: TestClient, app_id: str) -> list[str]:
    resp = client.get(f"/api/applications/{app_id}/activity", headers=AUTH)
    assert resp.status_code == 200
    return [e["kind"] for e in resp.json()]


def test_column_move_records_activity_event(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    job_id = _seed_job(app)
    app_id = _seed_ready_packet(app, job_id)
    resp = client.patch(
        f"/api/applications/{app_id}", headers=AUTH, json={"column": "applied"}
    )
    assert resp.status_code == 200
    body = client.get(f"/api/applications/{app_id}/activity", headers=AUTH).json()
    moves = [e for e in body if e["kind"] == "column_change"]
    assert len(moves) == 1
    assert moves[0]["label"] == "Moved from Saved to Applied"


def test_notes_edit_records_activity_event(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    job_id = _seed_job(app)
    app_id = _seed_ready_packet(app, job_id)
    client.patch(
        f"/api/applications/{app_id}", headers=AUTH,
        json={"notes_markdown": "Followed up with the recruiter."},
    )
    kinds = _activity_kinds(client, app_id)
    assert kinds.count("notes") == 1
    # Re-sending the same notes value adds no second entry (only-on-change).
    client.patch(
        f"/api/applications/{app_id}", headers=AUTH,
        json={"notes_markdown": "Followed up with the recruiter."},
    )
    assert _activity_kinds(client, app_id).count("notes") == 1


def test_archive_and_unarchive_record_activity_events(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    job_id = _seed_job(app)
    app_id = _seed_ready_packet(app, job_id)
    client.patch(f"/api/applications/{app_id}", headers=AUTH, json={"archived": True})
    client.patch(f"/api/applications/{app_id}", headers=AUTH, json={"archived": False})
    kinds = _activity_kinds(client, app_id)
    assert kinds.count("archive") == 1
    assert kinds.count("unarchive") == 1


def test_no_op_patch_records_no_event(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    job_id = _seed_job(app)
    app_id = _seed_ready_packet(app, job_id)
    # Re-assert the current column → no change → no event.
    client.patch(f"/api/applications/{app_id}", headers=AUTH, json={"column": "saved"})
    kinds = _activity_kinds(client, app_id)
    assert "column_change" not in kinds


# ---------------------------------------------------------------------------
# embedded job on the ApplicationDTO (US-TR-01 / US-TR-03 — "(job removed)" fix)
# ---------------------------------------------------------------------------


def test_application_dto_embeds_its_job(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """The Tracker must never depend on a capped client-side jobs join: every
    ApplicationDTO carries its own job (title/company/url), list + single."""
    app, client = app_client
    job_id = _seed_job(app, score=80)
    app_id = _seed_ready_packet(app, job_id)

    rows = client.get("/api/applications", headers=AUTH).json()
    row = next(r for r in rows if r["id"] == app_id)
    assert row["job"] is not None
    assert row["job"]["id"] == job_id
    assert row["job"]["title"] == "Backend Engineer"
    assert row["job"]["company"] == "Glean"
    assert row["job"]["score"]["score_0_100"] == 80

    single = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert single["job"]["id"] == job_id
    assert single["job"]["title"] == "Backend Engineer"



# ---------------------------------------------------------------------------
# Exclusive intent (docs/internal/roadmap.md §5.1) — none | referral | apply
# ---------------------------------------------------------------------------


def _saved_application(app: FastAPI, client: TestClient) -> str:
    """A Saved card with generation switched off (intent tests need no packet)."""
    job_id = _seed_job(app, url="https://ex.co/j/intent")
    resp = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job_id, "generate_resume": False, "generate_cover": False},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_intent_defaults_to_none_and_round_trips(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app_id = _saved_application(app, client)
    got = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert got["intent"] == "none"

    for value in ("referral", "apply", "none"):
        resp = client.patch(
            f"/api/applications/{app_id}", headers=AUTH, json={"intent": value}
        )
        assert resp.status_code == 200
        assert resp.json()["intent"] == value


def test_intent_is_exclusive_setting_one_replaces_the_other(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """§5.1: choosing `referral` clears `apply` and vice versa — the single
    stored value IS the exclusivity guarantee; the API always exposes exactly
    one authoritative intent."""
    app, client = app_client
    app_id = _saved_application(app, client)
    client.patch(f"/api/applications/{app_id}", headers=AUTH, json={"intent": "referral"})
    resp = client.patch(
        f"/api/applications/{app_id}", headers=AUTH, json={"intent": "apply"}
    )
    assert resp.json()["intent"] == "apply"  # referral fully replaced
    listing = client.get("/api/applications", headers=AUTH).json()
    assert [a["intent"] for a in listing if a["id"] == app_id] == ["apply"]


def test_intent_rejects_unknown_values(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app_id = _saved_application(app, client)
    resp = client.patch(
        f"/api/applications/{app_id}", headers=AUTH, json={"intent": "autopilot"}
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# attach / detach a document to an EXISTING application (the Upload button on
# the Tailored resume + Cover letter editors — post-creation parity with the
# manual-add attach). docstore.validate is extension+size only, so fake bytes
# with a real extension exercise the store/link path without a real parser.
# ---------------------------------------------------------------------------


def _attach(
    client: TestClient, app_id: str, kind: str, filename: str, data: bytes, mime: str
):
    return client.post(
        f"/api/applications/{app_id}/documents",
        headers=AUTH,
        data={"kind": kind},
        files={"file": (filename, data, mime)},
    )


def test_attach_document_links_file_to_existing_application(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    resp = _attach(
        client, app_id, "tailored_resume", "my-resume.pdf", b"%PDF-1.4 fake", "application/pdf"
    )
    assert resp.status_code == 201
    docs = resp.json()["documents"]
    assert len(docs) == 1
    assert docs[0]["kind"] == "tailored_resume"
    assert docs[0]["original_filename"] == "my-resume.pdf"
    # Persisted + downloadable verbatim.
    got = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert [d["kind"] for d in got["documents"]] == ["tailored_resume"]
    blob = client.get(f"/api/documents/{docs[0]['document_id']}", headers=AUTH)
    assert blob.status_code == 200
    assert blob.content == b"%PDF-1.4 fake"


def test_attach_replaces_prior_file_for_same_kind(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    _attach(client, app_id, "tailored_resume", "first.pdf", b"%PDF first", "application/pdf")
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    resp = _attach(client, app_id, "tailored_resume", "second.docx", b"PK\x03\x04 two", docx)
    docs = resp.json()["documents"]
    assert len(docs) == 1  # one resume slot per card — replaced, not appended
    assert docs[0]["original_filename"] == "second.docx"


def test_attach_resume_and_cover_coexist(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    _attach(client, app_id, "tailored_resume", "r.pdf", b"%PDF r", "application/pdf")
    resp = _attach(client, app_id, "cover_letter", "c.pdf", b"%PDF c", "application/pdf")
    assert {d["kind"] for d in resp.json()["documents"]} == {
        "tailored_resume",
        "cover_letter",
    }


def test_attach_accepts_odt_and_pages(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    assert _attach(
        client, app_id, "tailored_resume", "r.odt", b"PK\x03\x04 odt",
        "application/vnd.oasis.opendocument.text",
    ).status_code == 201
    assert _attach(
        client, app_id, "cover_letter", "c.pages", b"PK\x03\x04 pages",
        "application/vnd.apple.pages",
    ).status_code == 201


def test_attach_rejects_unsupported_type(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    resp = _attach(client, app_id, "tailored_resume", "photo.png", b"\x89PNG", "image/png")
    assert resp.status_code == 422


def test_attach_rejects_bad_kind(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    resp = _attach(client, app_id, "banner", "r.pdf", b"%PDF", "application/pdf")
    assert resp.status_code == 422


def test_attach_404_for_missing_application(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _, client = app_client
    resp = _attach(client, "no-such-app", "tailored_resume", "r.pdf", b"%PDF", "application/pdf")
    assert resp.status_code == 404


def test_detach_removes_the_link(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    _attach(client, app_id, "tailored_resume", "r.pdf", b"%PDF r", "application/pdf")
    _attach(client, app_id, "cover_letter", "c.pdf", b"%PDF c", "application/pdf")
    resp = client.delete(
        f"/api/applications/{app_id}/documents/tailored_resume", headers=AUTH
    )
    assert resp.status_code == 200
    assert [d["kind"] for d in resp.json()["documents"]] == ["cover_letter"]


def test_detach_rejects_bad_kind(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    app_id = _seed_ready_packet(app, _seed_job(app))
    resp = client.delete(
        f"/api/applications/{app_id}/documents/banner", headers=AUTH
    )
    assert resp.status_code == 422
