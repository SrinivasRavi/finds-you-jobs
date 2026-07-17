"""Covers: US-SET-12 / FR-SET-11 — user-editable LLM prompts.

The maintainer pulled user-configurable prompts into P1 (architecture §11,
2026-07-13). Exercises the whole seam:

- the file-based override store (get/set/reset round-trip in the app-data dir);
- the operation entrypoints passing the override through to the module (a
  capturing fake engine proves the *system prompt* the module runs is the
  override when set, and the shipped default after reset — the skill_md /
  system_prompt seam, §5);
- the HTTP surface (list / set / reset, unknown-kind 404, empty-markdown 422).

No live LLM or network — the engine is a capturing fake.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.main import create_app
from sidecar.app.prompt_overrides import (
    PROMPT_KINDS,
    default_md,
    get_override,
    list_prompts,
    reset,
    set_override,
)
from sidecar.app.registry import EngineRegistry, OperationContext
from sidecar.app.registry.operations import extract_entrypoint, score_entrypoint
from sidecar.app.security import SESSION_KEY_ENV
from sidecar.modules._shared.claude_engine import EngineUsage

# Inlined from the prior repository's test_a4_integration (not carried here).
SCORE_OUT = (
    "===SCORE===\n77\n===REASONS===\n- Strong backend overlap\n"
    "- Relocation matches\n===BREAKDOWN===\nRequirement | Match\n--- | ---\nJava | yes\n"
)


def _seed_profile_and_job(db: Database, *, url: str = "https://ex.co/j/1") -> str:
    with db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer with Java, Python, relocation-ready.")
        job = repos.jobs.create(
            canonical_url=url, title="Backend Engineer", company="Glean",
            location="Bengaluru",
            description=(
                "We are hiring a Backend Engineer to build distributed services in "
                "Java and Python. You will own APIs, work with Postgres and Kafka, "
                "and collaborate with product teams. Requires 5+ years of backend "
                "experience, strong system-design skills, and a track record of "
                "shipping reliable services at scale. Relocation to Bengaluru offered."
            ),
            source_adapter="greenhouse",
        )
        return job.id

TOKEN = "test-token-prompts"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# A profiler-shaped output (the extract op parses a JSON object).
EXTRACT_OUT = '{"name": "Tenet Loader", "email": "t@ex.co"}'


class CapturingEngine:
    """Records every system prompt it is handed; returns a canned output."""

    def __init__(self, output: str) -> None:
        self.output = output
        self.system_prompts: list[str] = []

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        self.system_prompts.append(system_prompt)
        return self.output, EngineUsage(
            internal_calls=1, tokens_in=100, tokens_out=40, usd=0.01,
            latency_ms=1000, model="fake-model",
        )


# ---------------------------------------------------------------------------
# file store round-trip (unit)
# ---------------------------------------------------------------------------


def test_override_store_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path))
    assert get_override("score") is None  # unset → default applies
    set_override("score", "# Custom scoring")
    assert get_override("score") == "# Custom scoring"
    # The override lives as a plain file under <data_dir>/prompts/.
    assert (tmp_path / "prompts" / "score.md").read_text() == "# Custom scoring"
    reset("score")
    assert get_override("score") is None
    # Reset is idempotent (no error when the file is already gone).
    reset("score")


def test_list_prompts_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path))
    set_override("tailor", "# Edited tailor")
    rows = {r["kind"]: r for r in list_prompts()}
    assert set(rows) == set(PROMPT_KINDS)
    assert rows["tailor"]["override_md"] == "# Edited tailor"
    assert rows["score"]["override_md"] is None
    # networker_draft is the one unrouted (prompt-only) kind.
    assert rows["networker_draft"]["routed"] is False
    assert rows["score"]["routed"] is True
    assert rows["score"]["default_md"] == default_md("score")


# ---------------------------------------------------------------------------
# entrypoint passthrough — the module runs the override, then the default
# ---------------------------------------------------------------------------


def test_score_entrypoint_passes_override_and_resets(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path))
    db = migrated_db
    job_id = _seed_profile_and_job(db)
    engine = CapturingEngine(SCORE_OUT)
    engines = EngineRegistry()
    engines.register("fake", engine)
    engines.route("score", engine="fake", model="fake-model")

    def run() -> None:
        with db.repos() as repos:
            op = repos.operations.create("score", {"job_id": job_id}).id
        score_entrypoint(
            OperationContext(
                kind="score", input_snapshot={"job_id": job_id},
                engine=engines.resolve("score"), db=db, operation_id=op,
            )
        )

    # 1) no override → the module runs the shipped default skill.
    run()
    assert engine.system_prompts[-1] == default_md("score")

    # 2) override set → the module runs the user's prompt verbatim.
    set_override("score", "MY CUSTOM SCORING PROMPT")
    run()
    assert engine.system_prompts[-1] == "MY CUSTOM SCORING PROMPT"

    # 3) reset → back to the shipped default.
    reset("score")
    run()
    assert engine.system_prompts[-1] == default_md("score")


def test_extract_entrypoint_passes_override(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The profiler seam is `system_prompt` (not `skill_md`) — same override path."""
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path))
    db = migrated_db
    _seed_profile_and_job(db)
    engine = CapturingEngine(EXTRACT_OUT)
    engines = EngineRegistry()
    engines.register("fake", engine)
    engines.route("extract", engine="fake", model="fake-model")

    set_override("extract", "EXTRACT WITH MY RULES")
    with db.repos() as repos:
        op = repos.operations.create("extract", {}).id
    extract_entrypoint(
        OperationContext(
            kind="extract", input_snapshot={},
            engine=engines.resolve("extract"), db=db, operation_id=op,
        )
    )
    assert engine.system_prompts[-1] == "EXTRACT WITH MY RULES"


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


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


def test_prompts_api_round_trip(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client

    listing = client.get("/api/settings/prompts", headers=AUTH).json()
    kinds = {row["kind"] for row in listing}
    assert kinds == set(PROMPT_KINDS)
    assert all(row["override_md"] is None for row in listing)

    # PUT sets an override.
    put = client.put(
        "/api/settings/prompts/tailor", headers=AUTH, json={"markdown": "# Mine"}
    )
    assert put.status_code == 200
    assert put.json()["override_md"] == "# Mine"

    # GET now reflects it.
    after = {r["kind"]: r for r in client.get("/api/settings/prompts", headers=AUTH).json()}
    assert after["tailor"]["override_md"] == "# Mine"

    # DELETE resets to the shipped default.
    delete = client.delete("/api/settings/prompts/tailor", headers=AUTH)
    assert delete.status_code == 200
    assert delete.json()["override_md"] is None
    after2 = {r["kind"]: r for r in client.get("/api/settings/prompts", headers=AUTH).json()}
    assert after2["tailor"]["override_md"] is None


def test_prompts_api_unknown_kind_404(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    assert client.put(
        "/api/settings/prompts/nope", headers=AUTH, json={"markdown": "x"}
    ).status_code == 404
    assert client.delete("/api/settings/prompts/nope", headers=AUTH).status_code == 404


def test_prompts_api_empty_markdown_422(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    assert client.put(
        "/api/settings/prompts/score", headers=AUTH, json={"markdown": "   "}
    ).status_code == 422
