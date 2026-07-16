"""Covers: profile + settings HTTP surface and the extract flow (FR-APP-01).

Full-app TestClient (lifespan runs the real migration + runner + engine
registry against a tmp data dir). The extract op runs against a monkeypatched
claude-cli engine — never a real subprocess.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app

TOKEN = "test-token-profile"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(token=TOKEN, original_ppid=None, data_dir=tmp_path / "data")
    with TestClient(app) as client:
        yield app, client


def _wait_for_kind_terminal(client: TestClient, kind: str) -> dict:
    deadline = time.monotonic() + 5
    last: dict = {}
    while time.monotonic() < deadline:
        ops = client.get("/api/operations", headers=AUTH).json()
        for op in ops:
            if op["kind"] == kind and op["state"] in ("succeeded", "failed", "cancelled"):
                return op
            if op["kind"] == kind:
                last = op
        time.sleep(0.02)
    raise AssertionError(f"no terminal {kind} operation: {last}")


# -- profile ----------------------------------------------------------------


def test_profile_upsert_and_get(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    assert client.get("/api/profile", headers=AUTH).json() is None
    up = client.post("/api/profile", headers=AUTH, json={"resume_markdown": "# Me"})
    assert up.status_code == 200 and up.json()["version"] == 1
    got = client.get("/api/profile", headers=AUTH)
    assert got.json()["resume_markdown"] == "# Me"


def test_profile_save_enqueues_extract_and_stubbed_engine_fails_verbatim(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Saving a master always enqueues `extract`; with the conftest-stubbed
    claude-cli the op fails fast with the typed engine error — verbatim in the
    ledger, never a silent hang."""
    _app, client = app_client
    client.post("/api/profile", headers=AUTH, json={"resume_markdown": "# Me"})
    op = _wait_for_kind_terminal(client, "extract")
    assert op["state"] == "failed"
    assert "claude-cli is stubbed out in unit tests" in op["error"]


def test_extract_writes_application_profile_with_fake_engine(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: manual re-extract runs the profiler against a fake engine
    and persists the grounded record onto the master profile."""
    import sidecar.modules._shared.claude_engine as ce

    payload = (
        '{"name": "Jane Doe", "first_name": "Jane", "last_name": "Doe",'
        ' "email": "jane@example.com", "phone": "", "location": "Berlin, Germany",'
        ' "country": "Germany", "work_authorization": "",'
        ' "links": {"github": "https://github.com/jane"}, "education": []}'
    )
    monkeypatch.setattr(
        ce.ClaudeCliEngine,
        "complete",
        lambda self, system, user: (payload, ce.EngineUsage(internal_calls=1, usd=0.001)),
    )

    _app, client = app_client
    client.post("/api/profile", headers=AUTH, json={"resume_markdown": "# Jane Doe"})
    resp = client.post("/api/profile/extract", headers=AUTH)
    assert resp.status_code == 202

    deadline = time.monotonic() + 5
    profile = None
    while time.monotonic() < deadline:
        profile = client.get("/api/profile", headers=AUTH).json()
        if profile and profile.get("application_profile"):
            break
        time.sleep(0.02)
    assert profile and profile["application_profile"] is not None
    record = profile["application_profile"]
    assert record["name"] == "Jane Doe"
    assert record["links"] == {"github": "https://github.com/jane"}
    assert record["source"] == "extracted"
    assert record["profile_version"] == 1
    # Grounding: absent facts stay empty, never invented.
    assert record["phone"] == "" and record["work_authorization"] == ""


def test_extract_without_master_404s(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    assert client.post("/api/profile/extract", headers=AUTH).status_code == 404


def test_patch_application_profile_stamps_edited(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    client.post("/api/profile", headers=AUTH, json={"resume_markdown": "# Me"})
    resp = client.patch(
        "/api/profile/application-profile",
        headers=AUTH,
        json={"name": "Edited Name", "email": "edit@example.com"},
    )
    assert resp.status_code == 200
    record = resp.json()["application_profile"]
    assert record["name"] == "Edited Name"
    assert record["source"] == "edited"


# -- settings ---------------------------------------------------------------


def test_settings_get_and_update(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    settings = client.get("/api/settings", headers=AUTH).json()
    assert settings["preferences"]["voyager_risk_marker_on"] is False
    assert settings["engines"] == []

    updated = client.post(
        "/api/settings", headers=AUTH, json={"freshness_days": 7, "voyager_risk_marker_on": True}
    )
    assert updated.status_code == 200
    prefs = updated.json()["preferences"]
    assert prefs["freshness_days"] == 7 and prefs["voyager_risk_marker_on"] is True


def test_settings_put_is_post_alias(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    updated = client.put("/api/settings", headers=AUTH, json={"freshness_days": 3})
    assert updated.status_code == 200
    assert updated.json()["preferences"]["freshness_days"] == 3
