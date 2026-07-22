"""Scoring modes (maintainer design 2026-07-22).

Two modes, no off-switch: "llm" (AI scoring, default) and "keyword" (the
on-device keyword scorer — free, instant, keyless). An AI failure persists a
keyword score as the visible grey fallback while the op stays failed and
retryable; a successful retry outranks it (display precedence LLM > keyword).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.main import create_app
from sidecar.app.registry import EngineRegistry, OperationContext
from sidecar.app.registry.operations import backfill_keyword_scores, score_entrypoint
from sidecar.app.registry.persistence import SCORER_IMPL, SCORER_IMPL_DETERMINISTIC
from sidecar.app.scheduler.planner import plan_score_new
from sidecar.modules._shared.claude_engine import EngineUsage

TOKEN = "test-token-scoring-modes"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}

SCORE_OUT = (
    "===SCORE===\n77\n===REASONS===\n- Strong backend overlap\n"
    "- Relocation matches\n===BREAKDOWN===\nRequirement | Match\n--- | ---\nJava | yes\n"
)


def _seed(db: Database, *, url: str = "https://ex.co/j/mode-1") -> str:
    with db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer with Java, Python, Kafka.")
        job = repos.jobs.create(
            canonical_url=url, title="Backend Engineer", company="Glean",
            location="Bengaluru",
            description=(
                "Backend Engineer building distributed services in Java and Python. "
                "APIs, Postgres, Kafka. Requires 5+ years of backend experience and "
                "strong system-design skills."
            ),
            source_adapter="greenhouse",
        )
        return job.id


class _OkEngine:
    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        return SCORE_OUT, EngineUsage(
            internal_calls=1, tokens_in=100, tokens_out=40, usd=0.01,
            latency_ms=5, model="fake-model",
        )


class _DeadEngine:
    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        raise RuntimeError("provider 429: rate limited")


def _ctx(db: Database, job_id: str, engine: Any | None) -> OperationContext:
    engines = EngineRegistry()
    resolved = None
    if engine is not None:
        engines.register("fake", engine)
        engines.route("score", engine="fake", model="fake-model")
        resolved = engines.resolve("score")
    with db.repos() as repos:
        op = repos.operations.create("score", {"job_id": job_id}).id
    return OperationContext(
        kind="score", input_snapshot={"job_id": job_id},
        engine=resolved, db=db, operation_id=op,
    )


def _scores(db: Database, job_id: str) -> dict[str, int]:
    with db.repos() as repos:
        profile = repos.profile.get_current()
        assert profile is not None
        version = profile.version
        out = {}
        for impl in (SCORER_IMPL, SCORER_IMPL_DETERMINISTIC):
            row = repos.job_scores.get_cached(job_id, version, impl)
            if row is not None:
                out[impl] = row.score_0_100
        return out


def test_keyword_mode_scores_without_any_engine(migrated_db: Database) -> None:
    """scoring_mode=keyword: the score op runs keyless — no engine at all —
    and persists a deterministic-impl score."""
    db = migrated_db
    job_id = _seed(db)
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "keyword"})
    outcome = score_entrypoint(_ctx(db, job_id, engine=None))
    assert outcome.engine == "on-device"
    scores = _scores(db, job_id)
    assert SCORER_IMPL_DETERMINISTIC in scores and SCORER_IMPL not in scores


def test_llm_failure_persists_keyword_fallback_and_still_fails(
    migrated_db: Database,
) -> None:
    """AI mode failover: the op raises (stays failed + retryable in Logs) but
    the keyword score is already persisted — the board shows grey, never a
    dead 'Score failed' pill."""
    db = migrated_db
    job_id = _seed(db)
    with pytest.raises(RuntimeError, match="429"):
        score_entrypoint(_ctx(db, job_id, engine=_DeadEngine()))
    scores = _scores(db, job_id)
    assert SCORER_IMPL_DETERMINISTIC in scores and SCORER_IMPL not in scores
    # Retry succeeds → the LLM score lands beside the fallback (display
    # precedence LLM > keyword is the API's job, asserted below).
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))
    scores = _scores(db, job_id)
    assert scores[SCORER_IMPL] == 77 and SCORER_IMPL_DETERMINISTIC in scores


def test_planner_counts_keyword_scores_as_scored(migrated_db: Database) -> None:
    """A job with only a keyword score (mode or fallback) is not re-planned on
    every scan — retry is deliberately manual (Logs → Retry)."""
    db = migrated_db
    job_id = _seed(db)
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "keyword"})
    assert len(plan_score_new(db)) == 1
    score_entrypoint(_ctx(db, job_id, engine=None))
    assert plan_score_new(db) == []


def test_backfill_scores_only_unscored_jobs(migrated_db: Database) -> None:
    db = migrated_db
    a = _seed(db, url="https://ex.co/j/bf-a")
    with db.repos() as repos:
        b = repos.jobs.create(
            canonical_url="https://ex.co/j/bf-b", title="Data Engineer",
            company="Acme", location="Remote", description="Python SQL pipelines.",
            source_adapter="greenhouse",
        ).id
    score_entrypoint(_ctx(db, a, engine=_OkEngine()))  # a: real AI score
    assert backfill_keyword_scores(db) == 1  # only b
    assert SCORER_IMPL_DETERMINISTIC not in _scores(db, a)
    assert SCORER_IMPL_DETERMINISTIC in _scores(db, b)


def test_api_serves_llm_over_keyword_and_settings_switch_backfills(tmp_path) -> None:
    """Through the real app: scorer_impl rides on the DTO, display precedence
    is LLM > keyword, and POSTing scoring_mode=keyword backfills the board in
    the same request."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        db = app.state.db
        a = _seed(db, url="https://ex.co/j/api-a")
        with db.repos() as repos:
            b = repos.jobs.create(
                canonical_url="https://ex.co/j/api-b", title="Data Engineer",
                company="Acme", location="Remote", description="Python SQL pipelines.",
                source_adapter="greenhouse",
            ).id
        score_entrypoint(_ctx(db, a, engine=_OkEngine()))
        # Switch to keyword mode via the settings API → b gets scored inline.
        resp = client.post(
            "/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "keyword"}}
        )
        assert resp.status_code == 200
        rows = {r["id"]: r for r in client.get("/api/jobs", headers=AUTH).json()}
        assert rows[a]["score"]["scorer_impl"] == SCORER_IMPL  # AI score kept
        assert rows[a]["score"]["score_0_100"] == 77
        assert rows[b]["score"]["scorer_impl"] == SCORER_IMPL_DETERMINISTIC
