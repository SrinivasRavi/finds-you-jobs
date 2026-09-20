"""A degraded boot starts with no scheduler, says so, and can be resumed.

The shell sets `FYJ_SCHEDULER_OFF=1` when the last 3 runs all ended the same
bad way, so the window opens and nothing queues itself into the same wall.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sidecar.app.main import create_app

TOKEN = "test-token"  # noqa: S105


@pytest.fixture
def _no_scheduler_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("FYJ_SCHEDULER_OFF", "1")
    yield


def _client(tmp_path: object) -> TestClient:
    app = create_app(token=TOKEN, data_dir=str(tmp_path))
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


def test_a_normal_boot_runs_the_scheduler(tmp_path: object) -> None:
    with _client(tmp_path) as client:
        body = client.get("/api/system/scheduler").json()
    assert body == {"running": True, "degradedBoot": False}


@pytest.mark.usefixtures("_no_scheduler_env")
def test_a_degraded_boot_starts_with_no_scheduler(tmp_path: object) -> None:
    with _client(tmp_path) as client:
        body = client.get("/api/system/scheduler").json()
    assert body == {"running": False, "degradedBoot": True}


@pytest.mark.usefixtures("_no_scheduler_env")
def test_resume_turns_background_work_back_on(tmp_path: object) -> None:
    with _client(tmp_path) as client:
        assert client.get("/api/system/scheduler").json()["running"] is False
        resumed = client.post("/api/system/scheduler/resume").json()
        assert resumed["running"] is True
        # The flag stays true: this boot WAS degraded, and saying otherwise
        # would erase the only record the user has of why work was paused.
        assert resumed["degradedBoot"] is True
        assert client.get("/api/system/scheduler").json()["running"] is True


@pytest.mark.usefixtures("_no_scheduler_env")
def test_resume_is_idempotent(tmp_path: object) -> None:
    with _client(tmp_path) as client:
        first = client.post("/api/system/scheduler/resume").json()
        second = client.post("/api/system/scheduler/resume").json()
    assert first == second == {"running": True, "degradedBoot": True}


def test_resume_on_an_already_running_scheduler_changes_nothing(tmp_path: object) -> None:
    with _client(tmp_path) as client:
        before = client.get("/api/system/scheduler").json()
        after = client.post("/api/system/scheduler/resume").json()
    assert before == after == {"running": True, "degradedBoot": False}
