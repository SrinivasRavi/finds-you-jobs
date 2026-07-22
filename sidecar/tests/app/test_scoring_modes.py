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
from sidecar.app.registry.operations import (
    backfill_keyword_scores,
    rescore_all_keyword,
    score_entrypoint,
)
from sidecar.app.registry.persistence import SCORER_IMPL, SCORER_IMPL_DETERMINISTIC
from sidecar.app.scheduler.planner import plan_score_new
from sidecar.modules._shared.claude_engine import EngineUsage

TOKEN = "test-token-scoring-modes"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RESUME = "# Test Candidate\n\nBackend engineer. Python, FastAPI, SQL, Kafka."

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
        version = repos.profile.get_current().version  # type: ignore[union-attr]
        snap = {"job_id": job_id, "profile_version": version}
        op = repos.operations.create("score", snap).id
    return OperationContext(
        kind="score", input_snapshot=snap,
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


def test_keyword_mode_plans_no_llm_the_floor_scores(migrated_db: Database) -> None:
    """In keyword mode the LLM planner plans nothing — the on-device floor
    (backfill_keyword_scores) does the scoring, no tokens spent."""
    db = migrated_db
    _seed(db)
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "keyword"})
    assert plan_score_new(db) == []
    assert backfill_keyword_scores(db) == 1  # the floor scores it


def test_llm_planner_upgrades_keyword_floored_jobs_but_not_failures(
    migrated_db: Database,
) -> None:
    """AI mode: a keyword-floored job is still planned for an LLM upgrade
    (floor doesn't count as done); a job whose LLM attempt FAILED is not
    auto-retried (its failed op excludes it — retry is manual)."""
    db = migrated_db
    a = _seed(db, url="https://ex.co/j/up-a")
    with db.repos() as repos:
        b = repos.jobs.create(
            canonical_url="https://ex.co/j/up-b", title="Data Engineer",
            company="Acme", location="Remote",
            description=(
                "Data Engineer to build and own batch and streaming pipelines in "
                "Python and SQL over Postgres and Kafka. Requires 5+ years of data "
                "engineering, strong modelling, and reliable delivery at scale."
            ),
            source_adapter="greenhouse",
        ).id
    # Floor both, then fail an LLM attempt on b.
    backfill_keyword_scores(db)
    with pytest.raises(RuntimeError):
        score_entrypoint(_ctx(db, b, engine=_DeadEngine()))
    planned = {p[1]["job_id"] for p in plan_score_new(db)}
    assert a in planned  # keyword-floored → still gets an LLM upgrade
    assert b not in planned  # failed attempt → not auto-retried


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


def test_display_shows_latest_version_and_stale_survives_a_resume_edit(
    migrated_db: Database,
) -> None:
    """A resume edit bumps the profile version but does NOT blank the board:
    the score from the highest available version is displayed, so a job scored
    at v1 keeps that score at v2 until a re-score lands (maintainer 2026-07-23)."""
    db = migrated_db
    job_id = _seed(db)
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))  # AI score at v1
    with db.repos() as repos:
        v1 = repos.profile.get_current().version  # type: ignore[union-attr]
        disp = repos.job_scores.latest_scores([job_id])[job_id]
        assert disp.profile_version == v1 and disp.scorer_impl == SCORER_IMPL
        # Edit the resume → v2. No re-score yet.
        repos.profile.upsert("# Test Candidate\n\nBackend engineer. Now with Go.")
        v2 = repos.profile.get_current().version  # type: ignore[union-attr]
        assert v2 == v1 + 1
        still = repos.job_scores.latest_scores([job_id])[job_id]
        assert still.profile_version == v1  # stale v1 AI score still shown
    # A keyword re-score at v2 now wins (latest version).
    rescore_all_keyword(db)
    with db.repos() as repos:
        after = repos.job_scores.latest_scores([job_id])[job_id]
        assert after.profile_version == v2
        assert after.scorer_impl == SCORER_IMPL_DETERMINISTIC


def test_llm_planner_does_not_auto_rescore_after_a_resume_edit(
    migrated_db: Database,
) -> None:
    """AI mode: a job already AI-scored at any version is not re-planned after a
    resume edit — re-scoring costs tokens and only happens via the explicit
    prompt, never silently on the next scan."""
    db = migrated_db
    job_id = _seed(db)
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))
    with db.repos() as repos:
        repos.profile.upsert("# Test Candidate\n\nBackend engineer. Kafka, Go.")
    assert plan_score_new(db) == []  # no auto re-score at the new version


def test_api_resume_edit_keyword_mode_auto_rescores_llm_mode_does_not(tmp_path) -> None:
    """Through the real app: editing the resume re-scores the whole board for
    free in keyword mode, but leaves prior scores untouched in AI mode (the
    frontend prompts and calls /api/jobs/rescore only on confirm)."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        db = app.state.db
        job_id = _seed(db)
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME})
        with db.repos() as repos:
            v0 = repos.profile.get_current().version  # type: ignore[union-attr]

        # AI mode (default): edit resume → NO new score; the board keeps prior.
        # (No score existed here, so it simply stays unscored — the point is no
        # keyword rescore is forced.)
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME + "\nGo."})
        with db.repos() as repos:
            assert repos.job_scores.latest_scores([job_id]) == {}

        # Switch to keyword mode, edit again → the board is re-scored for free.
        client.post(
            "/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "keyword"}}
        )
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME + "\nRust."})
        with db.repos() as repos:
            v_new = repos.profile.get_current().version  # type: ignore[union-attr]
            disp = repos.job_scores.latest_scores([job_id])[job_id]
            assert v_new > v0
            assert disp.profile_version == v_new
            assert disp.scorer_impl == SCORER_IMPL_DETERMINISTIC

        # AI re-score endpoint enqueues one score op per active job.
        client.post("/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "llm"}})
        r = client.post("/api/jobs/rescore", headers=AUTH)
        assert r.status_code == 200 and r.json()["queued"] == 1


# ---------------------------------------------------------------------------
# Re-score consent flow (maintainer 2026-07-23): a score is a cache row keyed
# by (job, profile_version, scorer_impl). Every AI re-score entry point fills
# cache MISSES only, and /api/jobs/rescore/preview counts the exact miss set
# the run would enqueue — the prompt's N always equals what actually runs.
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Any) -> Any:
    return create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )


def _score_ops_for(db: Database, job_id: str) -> int:
    """How many `score` operation rows exist for one job (any state)."""
    with db.repos() as repos:
        ops = repos.operations.list_by_kind_states(
            "score", {"queued", "running", "succeeded", "failed", "cancelled"}
        )
        return sum(
            1
            for op in ops
            if isinstance(op.input_snapshot, dict)
            and op.input_snapshot.get("job_id") == job_id
        )


def test_rescore_preview_and_run_fill_only_missing_ai_scores(tmp_path) -> None:
    """Two jobs, one already AI-scored at the current version: the preview
    reports 1 to score / 1 cached, and a confirmed run enqueues exactly the
    missing one — a re-score never re-spends tokens on a cache hit."""
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        a = _seed(db, url="https://ex.co/j/prev-a")
        with db.repos() as repos:
            b = repos.jobs.create(
                canonical_url="https://ex.co/j/prev-b", title="Data Engineer",
                company="Acme", location="Remote", description="Python SQL pipelines.",
                source_adapter="greenhouse",
            ).id
        score_entrypoint(_ctx(db, a, engine=_OkEngine()))  # a: AI score, current version

        prev = client.get("/api/jobs/rescore/preview", headers=AUTH).json()
        assert prev == {"toScore": 1, "cached": 1}

        r = client.post("/api/jobs/rescore", headers=AUTH)
        assert r.status_code == 200
        assert r.json() == {"queued": 1, "skipped": 1}
        # a's only score op is the _ctx one above — the re-score did NOT
        # enqueue a second op for the cache hit; b got its op.
        assert _score_ops_for(db, a) == 1
        assert _score_ops_for(db, b) == 1


def test_settings_switch_to_llm_enqueues_nothing_server_side(tmp_path) -> None:
    """Switching Scoring keyword→AI is a pure settings write — the server
    never spends tokens on its own; the frontend previews and asks first."""
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        _seed(db, url="https://ex.co/j/switch-a")
        client.post(
            "/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "keyword"}}
        )
        client.post(
            "/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "llm"}}
        )
        with db.repos() as repos:
            assert repos.operations.score_states_by_job() == {}


def test_resume_upsert_identical_content_keeps_version(migrated_db: Database) -> None:
    """Saving the resume unchanged bumps nothing — no new version, so no
    phantom 'Re-score N jobs?' prompt after a save that changed nothing."""
    db = migrated_db
    with db.repos() as repos:
        v1 = repos.profile.upsert(RESUME).version
        assert repos.profile.upsert(RESUME).version == v1
        assert repos.profile.upsert(RESUME + "\nGo.").version == v1 + 1


def test_restore_from_trash_reenqueues_by_mode(tmp_path) -> None:
    """Restore keeps a good score and re-scores per the CURRENT mode: keyword
    mode with a current-version keyword row enqueues nothing; AI mode with only
    a keyword floor enqueues the AI upgrade (the retry path, US-JB-06)."""
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        job_id = _seed(db, url="https://ex.co/j/restore-a")
        with db.repos() as repos:
            url = repos.jobs.get(job_id).canonical_url  # type: ignore[union-attr]
        # Keyword mode: the switch backfills the floor at the current version.
        client.post(
            "/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "keyword"}}
        )
        client.patch(f"/api/jobs/{job_id}", headers=AUTH, json={"feed_state": "removed"})
        client.post(
            "/api/jobs", headers=AUTH,
            json={"canonical_url": url, "title": "Backend Engineer"},
        )
        assert _score_ops_for(db, job_id) == 0  # keyword row is current — no op

        # AI mode: the keyword floor is only a floor — restore enqueues the
        # AI upgrade for a job with no AI score at the current version.
        client.post("/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "llm"}})
        client.patch(f"/api/jobs/{job_id}", headers=AUTH, json={"feed_state": "removed"})
        client.post(
            "/api/jobs", headers=AUTH,
            json={"canonical_url": url, "title": "Backend Engineer"},
        )
        assert _score_ops_for(db, job_id) == 1


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
