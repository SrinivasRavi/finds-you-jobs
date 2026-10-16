"""Shared fixtures for the core storage/runner/API tests."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.db.migrate import upgrade_to_head
from sidecar.app.main import create_app

from ._support import TOKEN


@pytest.fixture(autouse=True)
def _no_real_llm_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a unit test spawn a real `claude -p`. Background ops that hit this
    fail fast with an EngineError, which is harmless; a test needing an engine
    overrides it."""
    from sidecar.modules._shared import claude_engine

    def _refuse(self, system_prompt: str, user_prompt: str):  # noqa: ANN001, ANN202, ARG001
        # retryable=False: fail fast, exactly as documented above — without it
        # the shared retry policy would backoff-retry the stub and slow tests.
        raise claude_engine.EngineError(
            "claude-cli is stubbed out in unit tests", retryable=False
        )

    monkeypatch.setattr(claude_engine.ClaudeCliEngine, "complete", _refuse)


@pytest.fixture(autouse=True)
def _no_real_liveness_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scan's daily liveness pass would read seeded jobs' pages live. Every read
    answers "unknown" here; liveness tests pass their own reader."""
    from sidecar.app.registry import liveness

    monkeypatch.setattr(liveness, "_default_reader", lambda: lambda url: (None, url, ""))


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'db.sqlite'}"


@pytest.fixture
def migrated_db(db_url: str) -> Iterator[Database]:
    """A fresh, migrated SQLite DB in a tmp dir (real Alembic migration)."""
    upgrade_to_head(db_url)
    db = Database(db_url)
    try:
        yield db
    finally:
        db.dispose()


def wait_for_state(
    db: Database, operation_id: str, target: str | set[str], *, timeout: float = 5.0
) -> str:
    """Poll an operation until it reaches `target` (a state or set of states)."""
    targets = {target} if isinstance(target, str) else set(target)
    deadline = time.monotonic() + timeout
    state: str | None = None
    while time.monotonic() < deadline:
        with db.repos() as repos:
            op = repos.operations.get(operation_id)
            state = op.state if op is not None else None
        if state in targets:
            return state  # type: ignore[return-value]
        time.sleep(0.02)
    raise AssertionError(f"operation {operation_id} never reached {targets} (last={state})")


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    """A booted app and its client on a throwaway data dir, scheduler off. Shared by
    10 modules (D-T2); the 9 that need more keep their own."""
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client
