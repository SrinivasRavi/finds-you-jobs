"""Covers: the Applier app integration (docs/internal/applier.md §8/§9).

  US-level behavior — Apply off the Tracker card creates a durable ApplyRun
  immediately (no pre-confirm modal), the op drives the jobapplier agent
  against a LOCAL fixture form, and the run lands ready_for_human with
  redacted field outcomes + on-disk screenshot evidence. Attestation moves
  the card to Applied; the generic operations endpoint refuses `apply`.

ZERO model calls, zero external traffic: the engine is the scripted
FakeApplyEngine injected through the op's dev knobs (FYJ_APPLY_DEV=1), the
job URL is a file:// fixture, and the browser is headless Chromium.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app

TOKEN = "test-token-apply"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}

FIXTURES = Path(__file__).parent.parent / "packages" / "jobapplier" / "fixtures"


def _make_client(tmp_path: Path) -> Generator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FastAPI, TestClient]]:
    monkeypatch.setenv("FYJ_APPLY_DEV", "1")
    # The op resolves the run dir from the app data dir.
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path / "data"))
    yield from _make_client(tmp_path)


def _action(tool: str, **args: object) -> str:
    return json.dumps({"tool": tool, **args})


def _seed_card(client: TestClient) -> tuple[str, str]:
    """Master profile + a fixture-form job + a Saved application."""
    resp = client.post(
        "/api/profile",
        headers=AUTH,
        json={"resume_markdown": "# Ada Lovelace\n\nBackend engineer."},
    )
    assert resp.status_code in (200, 201)
    job = client.post(
        "/api/jobs",
        headers=AUTH,
        json={
            "canonical_url": (FIXTURES / "form.html").as_uri(),
            "title": "Staff Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Own the monolith.",
            "source_adapter": "paste-url",
        },
    ).json()
    application = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job["id"], "generate_resume": False, "generate_cover": False},
    ).json()
    return job["id"], application["id"]


def _wait_terminal(client: TestClient, run_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    run: dict = {}
    while time.monotonic() < deadline:
        run = client.get(f"/api/apply-runs/{run_id}", headers=AUTH).json()
        if run["status"] not in ("queued", "waiting_for_packet", "running"):
            return run
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} never landed: {run}")


# The scripted flow mirrors the package happy path, but the ids are computed
# server-side per observation — the script uses element-id-free heuristics via
# a tiny templating trick: the op's dev script accepts literal replies only,
# so we script against the STABLE fixture (ids assigned in document order).
# Document order in form.html: e1 form? … — instead of guessing, the script
# fills by trying e-ids in order; the loop tolerates rejected/failed steps and
# the test asserts on the terminal contract, not per-step success.
def _happy_script() -> list[str]:
    return [
        _action("fill", element_id="e2", value="Ada Lovelace"),
        _action("fill", element_id="e3", value="ada@example.com"),
        _action("finish", reason="grounded fields filled; remainder left for review"),
    ]


def test_apply_run_lands_ready_for_human(app_client) -> None:
    _app, client = app_client
    _job_id, app_id = _seed_card(client)

    resp = client.post(
        f"/api/applications/{app_id}/apply",
        headers=AUTH,
        json={
            "dev": {
                "engine_script": _happy_script(),
                "allow_local": True,
                "headed": False,
                "review_wait_s": 0,
            }
        },
    )
    assert resp.status_code == 202, resp.text
    run = resp.json()
    assert run["status"] == "queued"  # honest: not started yet (2026-07-17)

    final = _wait_terminal(client, run["id"])
    assert final["status"] == "ready_for_human", final
    assert final["final_url"].endswith("form.html")
    assert final["screenshot_count"] >= 2
    assert final["usage"]["calls"] == 3
    # Redacted evidence only: labels and outcomes, never values.
    assert not re.search(r"ada@example\.com", json.dumps(final["fields"]))

    # Evidence PNGs are served by index, path-free.
    shot = client.get(f"/api/apply-runs/{run['id']}/screenshots/0", headers=AUTH)
    assert shot.status_code == 200
    assert shot.content[:8] == b"\x89PNG\r\n\x1a\n"

    # The card settled the exclusive intent and surfaces the latest run.
    card = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert card["intent"] == "apply"
    assert card["applyRunStatus"] == "ready_for_human"
    assert card["applyRunId"] == run["id"]

    # History endpoint sees exactly one immutable run.
    runs = client.get(f"/api/applications/{app_id}/apply-runs", headers=AUTH).json()
    assert [r["id"] for r in runs] == [run["id"]]


def test_attest_submitted_moves_card_to_applied(app_client) -> None:
    _app, client = app_client
    _job_id, app_id = _seed_card(client)
    run = client.post(
        f"/api/applications/{app_id}/apply",
        headers=AUTH,
        json={
            "dev": {
                "engine_script": [_action("finish", reason="filled")],
                "allow_local": True,
                "headed": False,
                "review_wait_s": 0,
            }
        },
    ).json()
    final = _wait_terminal(client, run["id"])
    assert final["status"] == "ready_for_human"

    attested = client.post(
        f"/api/apply-runs/{run['id']}/attest",
        headers=AUTH,
        json={"submitted": True},
    ).json()
    assert attested["status"] == "submitted"
    assert attested["submit_evidence"] == "user_attested"
    card = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert card["column"] == "applied"
    assert card["applied_via"] == "applier"


def test_attest_didnt_submit_leaves_card(app_client) -> None:
    _app, client = app_client
    _job_id, app_id = _seed_card(client)
    run = client.post(
        f"/api/applications/{app_id}/apply",
        headers=AUTH,
        json={
            "dev": {
                "engine_script": [_action("finish", reason="filled")],
                "allow_local": True,
                "headed": False,
                "review_wait_s": 0,
            }
        },
    ).json()
    _wait_terminal(client, run["id"])
    kept = client.post(
        f"/api/apply-runs/{run['id']}/attest",
        headers=AUTH,
        json={"submitted": False},
    ).json()
    assert kept["status"] == "ready_for_human"
    card = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert card["column"] == "saved"


def test_apply_cannot_be_enqueued_generically(app_client) -> None:
    _app, client = app_client
    resp = client.post("/api/operations/apply", headers=AUTH, json={})
    assert resp.status_code == 422


def test_closed_posting_blocks_with_zero_model_calls(app_client) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/profile",
        headers=AUTH,
        json={"resume_markdown": "# Ada Lovelace\n\nBackend engineer."},
    )
    assert resp.status_code in (200, 201)
    job = client.post(
        "/api/jobs",
        headers=AUTH,
        json={
            "canonical_url": (FIXTURES / "closed.html").as_uri(),
            "title": "Staff Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Own the monolith.",
            "source_adapter": "paste-url",
        },
    ).json()
    app_id = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job["id"], "generate_resume": False, "generate_cover": False},
    ).json()["id"]
    run = client.post(
        f"/api/applications/{app_id}/apply",
        headers=AUTH,
        json={
            "dev": {
                # Any model call would consume a step; an empty script raises —
                # proving zero tokens are spent on a dead posting.
                "engine_script": [_action("finish", reason="unreachable")],
                "allow_local": True,
                "headed": False,
                "review_wait_s": 0,
            }
        },
    ).json()
    final = _wait_terminal(client, run["id"])
    assert final["status"] == "blocked"
    assert final["blockers"][0]["kind"] == "posting_closed"
    assert final["usage"]["calls"] == 0
