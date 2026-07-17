"""Covers: resume ingestion for onboarding (FR-OB-04 / US-OB-02).

Drives `sidecar/app/api/ingest.py` through the real app (TestClient → lifespan):

- `.md` / `.txt` upload returns the file's text verbatim (multipart route works);
- a `.pdf` upload extracts selectable text via pypdf (real fixture PDF);
- an empty file / undecodable bytes / unsupported type / image-only PDF all
  return 422 with a paste-instead message — never a silent empty draft;
- the reviewed text round-trips through `POST /api/profile` and back.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.security import SESSION_KEY_ENV

TOKEN = "test-token-ingest"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FastAPI, TestClient]]:
    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


# ---------------------------------------------------------------------------
# tiny hand-built PDF fixtures (no third-party PDF writer needed)
# ---------------------------------------------------------------------------


def _make_pdf(text_lines: list[str]) -> bytes:
    """A minimal single-page PDF whose content stream draws `text_lines` as
    selectable Helvetica text — enough for pypdf's `extract_text` to read back.
    xref byte-offsets are computed so the file is structurally valid."""
    show = "\n".join(
        f"BT /F1 12 Tf 72 {720 - i * 16} Td ({line}) Tj ET" for i, line in enumerate(text_lines)
    )
    content = show.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
        + f"startxref\n{xref_pos}\n%%EOF".encode()
    )
    return bytes(out)


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_ingest_markdown(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    md = "# Jane Doe\n\n- Senior Backend Engineer\n- 8 years Python\n"
    resp = client.post(
        "/api/profile/ingest",
        headers=AUTH,
        files={"file": ("resume.md", md.encode(), "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == md.strip()
    assert body["filename"] == "resume.md"
    assert body["chars"] == len(md.strip())


def test_ingest_txt(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/profile/ingest",
        headers=AUTH,
        files={"file": ("resume.txt", b"Plain text resume body", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "Plain text resume body"


def test_ingest_pdf_extracts_text(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    pdf = _make_pdf(["Jane Doe", "Senior Backend Engineer", "Python Rust Kubernetes"])
    resp = client.post(
        "/api/profile/ingest",
        headers=AUTH,
        files={"file": ("resume.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    text = resp.json()["text"]
    assert "Jane Doe" in text
    assert "Senior Backend Engineer" in text
    assert "Kubernetes" in text


# ---------------------------------------------------------------------------
# honest failures (never persist garbage)
# ---------------------------------------------------------------------------


def test_ingest_empty_file_refused(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/profile/ingest",
        headers=AUTH,
        files={"file": ("resume.md", b"", "text/markdown")},
    )
    assert resp.status_code == 422
    assert "paste" in resp.json()["detail"].lower()


def test_ingest_undecodable_text_refused(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    # Invalid UTF-8 bytes with a .txt suffix → undecodable, refuse (not garbage).
    resp = client.post(
        "/api/profile/ingest",
        headers=AUTH,
        files={"file": ("resume.txt", b"\xff\xfe\x00\x80garbage", "text/plain")},
    )
    assert resp.status_code == 422
    assert "paste" in resp.json()["detail"].lower()


def test_ingest_unsupported_type_refused(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/profile/ingest",
        headers=AUTH,
        files={"file": ("resume.docx", b"PK\x03\x04 not a docx", "application/octet-stream")},
    )
    assert resp.status_code == 422
    assert "paste" in resp.json()["detail"].lower()


def test_ingest_image_only_pdf_refused(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    # A structurally-valid PDF page with no text content stream → nothing to
    # extract → refuse rather than yield an empty draft.
    pdf = _make_pdf([])
    resp = client.post(
        "/api/profile/ingest",
        headers=AUTH,
        files={"file": ("scan.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 422
    assert "paste" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# reviewed text persists through the existing profile route
# ---------------------------------------------------------------------------


def test_reviewed_text_persists_via_profile(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    md = "# Jane Doe\n\nSenior Backend Engineer"
    ingest = client.post(
        "/api/profile/ingest",
        headers=AUTH,
        files={"file": ("resume.md", md.encode(), "text/markdown")},
    ).json()

    # No profile before Finish (guard invariant: MasterProfile exists ⟺ onboarded).
    assert client.get("/api/profile", headers=AUTH).json() is None

    saved = client.post(
        "/api/profile", headers=AUTH, json={"resume_markdown": ingest["text"]}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["resume_markdown"] == md.strip()

    got = client.get("/api/profile", headers=AUTH).json()
    assert got is not None
    assert got["resume_markdown"] == md.strip()
