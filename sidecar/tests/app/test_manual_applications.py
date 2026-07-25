"""Covers: the "Add a job application" vertical (FR-TR manual-add).

Through the real app (TestClient → lifespan → real migration):

- POST /api/applications/manual creates a job + an `origin=manual` card in a
  post-referral stage, with optional resume/cover uploads.
- Uploaded documents are content-addressed and deduped (identical bytes → one
  `documents` row and ONE blob on disk), downloadable verbatim.
- The Tracker source filter's discriminator: `origin` on the ApplicationDTO
  ("manual" vs "discovered").
- Guards: already-tracked job → 409; bad stage / unsupported type / too-large
  upload → 422; unknown document → 404.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app import documents as docstore
from sidecar.app.main import create_app

TOKEN = "test-token-manual"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


def _blob_dir(app: FastAPI) -> Path:
    return app.state.db.data_dir / "documents"


# ---------------------------------------------------------------------------
# create — the plain path (no uploads)
# ---------------------------------------------------------------------------


def test_manual_application_creates_manual_origin_card(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    r = client.post(
        "/api/applications/manual",
        data={
            "canonical_url": "https://ex.co/careers/eng-42",
            "title": "Staff Engineer",
            "company": "Acme",
            "location": "Remote",
            "column": "applied",
            "notes_markdown": "Applied via their site.",
        },
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["origin"] == "manual"
    assert body["column"] == "applied"
    assert body["applied_via"] == "manual"
    assert body["notes_markdown"] == "Applied via their site."
    assert body["job"]["title"] == "Staff Engineer"
    assert body["job"]["company"] == "Acme"
    assert body["documents"] == []


def test_manual_application_shows_up_in_list_distinguishable_by_origin(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    # A discovered card (normal flow): seed a job + application directly.
    with app.state.db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="https://ex.co/disc/1", title="Discovered Role",
            company="Disc", source_adapter="greenhouse",
        )
        repos.applications.create(job.id, column="saved")
    # A manual card.
    client.post(
        "/api/applications/manual",
        data={"canonical_url": "https://ex.co/manual/1", "title": "Manual Role"},
        headers=AUTH,
    )
    rows = client.get("/api/applications", headers=AUTH).json()
    by_origin = {row["origin"] for row in rows}
    assert by_origin == {"discovered", "manual"}
    manual = [r for r in rows if r["origin"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["job"]["title"] == "Manual Role"


# ---------------------------------------------------------------------------
# uploads — attach, download, dedup
# ---------------------------------------------------------------------------


def test_manual_application_attaches_and_downloads_documents(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    resume_bytes = b"%PDF-1.4 resume bytes"
    cover_bytes = b"cover letter text"
    r = client.post(
        "/api/applications/manual",
        data={"canonical_url": "https://ex.co/j/withdocs", "title": "Role"},
        files={
            "resume": ("my-resume.pdf", resume_bytes, "application/pdf"),
            "cover": ("cover.txt", cover_bytes, "text/plain"),
        },
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    docs = {d["kind"]: d for d in r.json()["documents"]}
    assert set(docs) == {"tailored_resume", "cover_letter"}
    assert docs["tailored_resume"]["original_filename"] == "my-resume.pdf"
    assert docs["tailored_resume"]["mime_type"] == "application/pdf"
    assert docs["tailored_resume"]["byte_size"] == len(resume_bytes)

    # Download serves the bytes verbatim with the original filename.
    dl = client.get(f"/api/documents/{docs['tailored_resume']['document_id']}", headers=AUTH)
    assert dl.status_code == 200
    assert dl.content == resume_bytes
    assert "my-resume.pdf" in dl.headers.get("content-disposition", "")


def test_identical_uploads_dedup_to_one_row_and_one_blob(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    same = b"the very same resume bytes"
    for i in (1, 2):
        r = client.post(
            "/api/applications/manual",
            data={"canonical_url": f"https://ex.co/j/dedup-{i}", "title": f"Role {i}"},
            files={"resume": ("resume.pdf", same, "application/pdf")},
            headers=AUTH,
        )
        assert r.status_code == 201, r.text

    # One Document row, one blob on disk — despite two applications referencing it.
    from sqlalchemy import func, select

    from sidecar.app.db.models import ApplicationDocument, Document

    with app.state.db.session() as session:
        doc_count = session.scalar(select(func.count()).select_from(Document))
        link_count = session.scalar(select(func.count()).select_from(ApplicationDocument))
    assert doc_count == 1
    assert link_count == 2
    blobs = list(_blob_dir(app).iterdir())
    assert len(blobs) == 1
    assert blobs[0].name == docstore.sha256_hex(same)


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def test_already_tracked_job_is_rejected(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with app.state.db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="https://ex.co/j/tracked", title="Tracked",
            company="T", source_adapter="lever",
        )
        repos.applications.create(job.id, column="saved")
    r = client.post(
        "/api/applications/manual",
        data={"canonical_url": "https://ex.co/j/tracked", "title": "Tracked"},
        headers=AUTH,
    )
    assert r.status_code == 409
    assert "already in your tracker" in r.json()["detail"]


def test_bad_stage_is_rejected(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    r = client.post(
        "/api/applications/manual",
        data={"canonical_url": "https://ex.co/j/badstage", "column": "saved"},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert "stage" in r.json()["detail"]


def test_unsupported_upload_type_is_rejected(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    r = client.post(
        "/api/applications/manual",
        data={"canonical_url": "https://ex.co/j/badtype"},
        files={"resume": ("resume.exe", b"MZ...", "application/octet-stream")},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert "allowed types" in r.json()["detail"]


def test_too_large_upload_is_rejected(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = app_client
    monkeypatch.setattr(docstore, "MAX_BYTES", 8)
    r = client.post(
        "/api/applications/manual",
        data={"canonical_url": "https://ex.co/j/toobig"},
        files={"resume": ("resume.pdf", b"way more than eight bytes", "application/pdf")},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert "limit" in r.json()["detail"]


def test_unknown_document_download_404(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    r = client.get("/api/documents/nonexistent-id", headers=AUTH)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# lifecycle parity — a manual card archives/restores/removes exactly like a
# discovered one (2026-07-24 customer bug class: origin must never gate the
# card lifecycle)
# ---------------------------------------------------------------------------


def _create_manual(client: TestClient, url: str, title: str) -> dict:
    r = client.post(
        "/api/applications/manual",
        data={"canonical_url": url, "title": title, "column": "applied"},
        files={"resume": ("resume.pdf", b"%PDF-1.4 submitted resume", "application/pdf")},
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_manual_application_archive_and_restore_lifecycle(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    created = _create_manual(client, "https://ex.co/j/lifecycle", "Lifecycle Role")
    app_id = created["id"]

    # Archive: 200, stamped, and the DTO still round-trips the manual extras
    # (origin + attached documents) — serialization must not assume a packet.
    r = client.patch(
        f"/api/applications/{app_id}", headers=AUTH, json={"archived": True}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived_at"] is not None
    assert body["origin"] == "manual"
    assert [d["kind"] for d in body["documents"]] == ["tailored_resume"]
    assert body["packetState"] == "none"

    # Excluded from the board list; present under include_archived.
    default_ids = {a["id"] for a in client.get("/api/applications", headers=AUTH).json()}
    assert app_id not in default_ids
    archived = client.get(
        "/api/applications?include_archived=true", headers=AUTH
    ).json()
    assert app_id in {a["id"] for a in archived}

    # Restore: back on the board, archived stamp cleared.
    r = client.patch(
        f"/api/applications/{app_id}", headers=AUTH, json={"archived": False}
    )
    assert r.status_code == 200
    assert r.json()["archived_at"] is None
    default_ids = {a["id"] for a in client.get("/api/applications", headers=AUTH).json()}
    assert app_id in default_ids


def test_retention_purge_handles_manual_card_children(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """FR-SYS-06: the retention purge must delete an archived manual card's FK
    children (document links, apply runs) — it used to IntegrityError on the
    first archived manual card and wedge the whole pass (2026-07-24)."""
    from datetime import timedelta

    from sidecar.app.db.base import now_utc
    from sidecar.app.registry.persistence import purge_archived_applications

    app, client = app_client
    created = _create_manual(client, "https://ex.co/j/purge-me", "Purge Role")
    app_id = created["id"]
    client.patch(f"/api/applications/{app_id}", headers=AUTH, json={"archived": True})
    with app.state.db.repos() as repos:
        repos.apply_runs.create(app_id, status="failed", phase="failed")
        repos.applications.update(
            app_id, archived_at=now_utc() - timedelta(days=90)
        )
    purged = purge_archived_applications(app.state.db, retention_days=30)
    assert app_id in purged
    remaining = client.get(
        "/api/applications?include_archived=true", headers=AUTH
    ).json()
    assert app_id not in {a["id"] for a in remaining}


def test_manual_application_delete_removes_card(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    created = _create_manual(client, "https://ex.co/j/delete-me", "Delete Role")
    app_id = created["id"]
    # Every FK child must go with the card — the uploaded-document link (part
    # of the create above) AND an apply run (both 500'd with IntegrityError
    # before the delete route purged them).
    with app.state.db.repos() as repos:
        repos.apply_runs.create(app_id, status="failed", phase="failed")
    r = client.delete(f"/api/applications/{app_id}", headers=AUTH)
    assert r.status_code == 204
    assert app_id not in {
        a["id"]
        for a in client.get(
            "/api/applications?include_archived=true", headers=AUTH
        ).json()
    }
    # Idempotence guard: a second delete is a clean 404, not a 500.
    assert client.delete(f"/api/applications/{app_id}", headers=AUTH).status_code == 404
