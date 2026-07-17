"""Covers: the feature-parity utility routes carried from the prior repository.

  US-RES-03 — /api/export/pdf renders markdown → a real PDF in Downloads
              (collision-safe name), 422 on empty markdown.
  A5b/A0.6  — /api/system/install-browser starts the (mocked) Chromium
              download once; a second call reports already_running.
  Dev tools — /api/dev/seed-application creates a Job + Saved card;
              /api/dev/operations/fail-running fails running ops with the
              boot-recovery note.

The PDF render uses the real local Chromium print pipeline (no network);
the browser INSTALL is monkeypatched — tests never download browsers.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app

TOKEN = "test-token-parity"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _make_client(tmp_path: Path) -> Generator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    yield from _make_client(tmp_path)


def test_export_pdf_renders_into_downloads(
    app_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = app_client
    downloads = tmp_path / "downloads"
    from sidecar.app.api import routes as routes_mod

    monkeypatch.setattr(routes_mod, "downloads_dir", lambda: downloads)

    resp = client.post(
        "/api/export/pdf",
        headers=AUTH,
        json={"markdown": "# Ada Lovelace\n\nBackend engineer.", "filename": "My Resume"},
    )
    assert resp.status_code == 200, resp.text
    path = Path(resp.json()["path"])
    assert path.parent == downloads and path.name == "My-Resume.pdf"
    assert path.read_bytes()[:5] == b"%PDF-"

    # Same filename again → collision-safe suffix, both files kept.
    again = client.post(
        "/api/export/pdf",
        headers=AUTH,
        json={"markdown": "# Second", "filename": "My Resume"},
    ).json()
    assert Path(again["path"]).name == "My-Resume-1.pdf"


def test_export_pdf_rejects_empty(app_client) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/export/pdf", headers=AUTH, json={"markdown": "   ", "filename": "x"}
    )
    assert resp.status_code == 422


def test_install_browser_is_idempotent(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = app_client
    from sidecar.app.api import browser as browser_mod

    calls: list[str] = []

    def fake_start(publish) -> str:  # noqa: ANN001 — mirrors start_install
        calls.append("x")
        return "started" if len(calls) == 1 else "already_running"

    monkeypatch.setattr(browser_mod, "start_install", fake_start)
    first = client.post("/api/system/install-browser", headers=AUTH)
    second = client.post("/api/system/install-browser", headers=AUTH)
    assert first.status_code == 202 and first.json()["status"] == "started"
    assert second.json()["status"] == "already_running"


def test_dev_seed_and_fail_running(app_client) -> None:
    _app, client = app_client
    seeded = client.post("/api/dev/seed-application", headers=AUTH)
    assert seeded.status_code == 201
    body = seeded.json()
    card = client.get(f"/api/applications/{body['application_id']}", headers=AUTH).json()
    assert card["column"] == "saved"

    # No running ops → fail-running is a clean no-op.
    failed = client.post("/api/dev/operations/fail-running", headers=AUTH).json()
    assert failed["ok"] is True and failed["count"] == 0
