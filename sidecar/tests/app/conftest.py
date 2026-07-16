"""Shared fixtures for the core storage/runner/API tests."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from sidecar.app.db import Database
from sidecar.app.db.migrate import upgrade_to_head


@pytest.fixture(autouse=True)
def _no_real_llm_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Background ops enqueued by routes (extract at profile-save) resolve the
    default claude-cli engine — never let a unit test spawn the real `claude -p`
    subprocess (cost + nondeterminism). Ops that hit this fail fast with a typed
    EngineError, which is harmless: route tests assert enqueueing, not op
    outcomes. A test that needs a working engine overrides this with its own
    fake."""
    from sidecar.modules._shared import claude_engine

    def _refuse(self, system_prompt: str, user_prompt: str):  # noqa: ANN001, ANN202, ARG001
        raise claude_engine.EngineError("claude-cli is stubbed out in unit tests")

    monkeypatch.setattr(claude_engine.ClaudeCliEngine, "complete", _refuse)


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
