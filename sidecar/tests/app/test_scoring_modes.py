"""Scoring modes (maintainer design 2026-07-22).

Two modes, no off-switch: "llm" (LLM scoring, default) and "keyword" (the
on-device keyword scorer — free, instant, keyless). An AI failure persists a
keyword score as the visible grey fallback while the op stays failed and
retryable; a successful retry outranks it (display precedence LLM > keyword).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.db.models import OP_ALL_STATES
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

# Job fixtures must clear `MIN_JD_CHARS` (200) or the planner skips them as
# too thin to score — the real floor, not a test artefact. Short one-liners were
# never representative: the shortest genuine description on the maintainer's
# install is 464 chars, and 248 rows sit at exactly 0.
REALISTIC_JD = (
    "Backend engineer role. You will build and operate Python services, design Post"
    "greSQL schemas, run workloads on AWS, and review other engineers' work. Requirem"
    "ents: strong Python, solid SQL, and production ownership experience."
)

TOKEN = "test-token-scoring-modes"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RESUME = "# Test Candidate\n\nBackend engineer. Python, FastAPI, SQL, Kafka."

SCORE_OUT = (
    "===SCORE===\n77\n===REASONS===\n- Strong backend overlap\n"
    "- Relocation matches\n===BREAKDOWN===\nRequirement | Match\n--- | ---\nJava | yes\n"
    "===FACTORS===\ncore_stack: 0.90 | Java and Python evidenced\n"
    "years: 1.00 | 9 against 5 required\nlocation_preference: 1.00 | Bengaluru is wanted\n"
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
                "strong system-design skills. You will own services end to end, from "
                "design review through on-call, and mentor engineers on the team."
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
    """The job's 1 score, keyed by the impl that wrote it (score-storage.md 4.1),
    under the enum names the rest of the suite already speaks."""
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        if job.score is None:
            return {}
        impl = SCORER_IMPL_DETERMINISTIC if job.score_method == "keyword" else SCORER_IMPL
        return {impl: job.score}


def _factors(db: Database, job_id: str) -> dict[str, SimpleNamespace]:
    """The job's factors, by axis, as attribute objects."""
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        return {f["factor"]: SimpleNamespace(**f) for f in job.score_factors}


def _settle_ops(db: Database) -> None:
    """Mark queued score ops failed, the way the runner would. These tests call
    `score_entrypoint` directly, so nothing else moves the op off `queued`, and
    the planner correctly refuses to double-enqueue a job whose score is still
    in flight."""
    with db.repos() as repos:
        for op in repos.operations.list_by_state("queued"):
            repos.operations.mark_failed(op.id, error="test harness")


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


def test_keyword_mode_writes_its_factor_rows_and_reads_the_users_locations(
    migrated_db: Database,
) -> None:
    """A score must land with its factor rows, or the UI can't explain it (scoring.md
    7.2). The job and the preference are both Bengaluru, so the location axis must
    match: the op carries the user's chips down to the scorer."""
    db = migrated_db
    job_id = _seed(db)
    with db.repos() as repos:
        repos.preferences.update(
            thresholds={"scoring_mode": "keyword"}, locations=["Bengaluru", "Remote"]
        )
    score_entrypoint(_ctx(db, job_id, engine=None))
    rows = _factors(db, job_id)
    # No level axis: neither the seeded title nor the seeded resume states a rung,
    # and an unassessed axis is absent rather than a middle value.
    assert set(rows) == {"core_stack", "years", "location_preference"}
    assert rows["location_preference"].value == 1.0
    assert "Bengaluru" in rows["location_preference"].evidence
    assert all(row.kind == "graded" for row in rows.values())
    assert all(0.0 <= row.value <= 1.0 for row in rows.values())


def test_a_rescore_replaces_the_factor_rows_rather_than_piling_up(
    migrated_db: Database,
) -> None:
    db = migrated_db
    job_id = _seed(db)
    with db.repos() as repos:
        repos.preferences.update(
            thresholds={"scoring_mode": "keyword"}, locations=["Bengaluru"]
        )
    score_entrypoint(_ctx(db, job_id, engine=None))
    _settle_ops(db)
    rescore_all_keyword(db)
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        rows = job.score_factors
    assert len(rows) == len({row["factor"] for row in rows})


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
    # Retry succeeds → the LLM score replaces the keyword floor (1 score per job).
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))
    assert _scores(db, job_id) == {SCORER_IMPL: 77}


def test_keyword_mode_plans_no_llm_the_floor_scores(migrated_db: Database) -> None:
    """In keyword mode the LLM planner plans nothing — the on-device floor
    (backfill_keyword_scores) does the scoring, no tokens spent."""
    db = migrated_db
    _seed(db)
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "keyword"})
    assert plan_score_new(db) == []
    assert backfill_keyword_scores(db) == 1  # the floor scores it


def test_llm_planner_upgrades_keyword_floored_jobs_and_retries_failures(
    migrated_db: Database,
) -> None:
    """AI mode: a keyword-floored job is still planned for an upgrade, and a failed
    attempt is planned again while the budget lasts (it once excluded the job for
    good). The bound is `Job.score_attempts`."""
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
                " You will own the warehouse, the orchestration layer, and on-call."
            ),
            source_adapter="greenhouse",
        ).id
    # Floor both, then fail an LLM attempt on b.
    backfill_keyword_scores(db)
    with pytest.raises(RuntimeError):
        score_entrypoint(_ctx(db, b, engine=_DeadEngine()))
    _settle_ops(db)
    planned = {p[1]["job_id"] for p in plan_score_new(db)}
    assert a in planned  # keyword-floored → still gets an LLM upgrade
    assert b in planned  # 1 failed attempt of 3 → comes back next tick


def test_backfill_scores_only_unscored_jobs(migrated_db: Database) -> None:
    db = migrated_db
    a = _seed(db, url="https://ex.co/j/bf-a")
    with db.repos() as repos:
        b = repos.jobs.create(
            canonical_url="https://ex.co/j/bf-b", title="Data Engineer",
            company="Acme", location="Remote", description=REALISTIC_JD,
            source_adapter="greenhouse",
        ).id
    score_entrypoint(_ctx(db, a, engine=_OkEngine()))  # a: real LLM score
    assert backfill_keyword_scores(db) == 1  # only b
    assert SCORER_IMPL_DETERMINISTIC not in _scores(db, a)
    assert SCORER_IMPL_DETERMINISTIC in _scores(db, b)


def test_llm_score_survives_a_resume_edit_and_its_keyword_sweep(
    migrated_db: Database,
) -> None:
    """A resume edit never blanks the board, and the keyword sweep it fires can
    never bury the LLM score. The 2 ratings are separate columns and the display
    rule prefers the AI one outright, so the defect that showed 26 where the AI
    had said 82 on 36 jobs is not expressible any more (S-C34)."""
    from sidecar.app.api import dto as dto_mod

    db = migrated_db
    job_id = _seed(db)
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        llm_score = job.score
        assert llm_score == 77
        assert dto_mod.job_score_dto(job).scorer_impl == SCORER_IMPL  # type: ignore[union-attr]
        repos.profile.upsert("# Test Candidate\n\nBackend engineer. Now with Go.")
    # The sweep never replaces a real score with a Keyword one (score-storage.md 4.2).
    rescore_all_keyword(db)
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        assert job.score_method == "llm"
        shown = dto_mod.job_score_dto(job)
        assert shown is not None
        assert shown.scorer_impl == SCORER_IMPL
        assert shown.score_0_100 == llm_score


def test_llm_planner_does_not_auto_rescore_after_a_resume_edit(
    migrated_db: Database,
) -> None:
    """AI mode: a job already LLM-scored at any version is not re-planned after a
    resume edit. An LLM score is version-agnostic (D1), so the prior score stays
    on the board and no tick ever re-spends on it."""
    db = migrated_db
    job_id = _seed(db)
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))
    with db.repos() as repos:
        repos.profile.upsert("# Test Candidate\n\nBackend engineer. Kafka, Go.")
    assert plan_score_new(db) == []  # no auto re-score at the new version


def test_api_a_major_edit_rescores_keyword_jobs_under_any_method(tmp_path) -> None:
    """Through the real app (score-storage.md 5.2): a minor edit rescores nothing,
    in any mode; a major edit (more than 300 characters added) rescores every Keyword
    score for free even while LLM scoring is selected, and leaves a real score alone.
    There is no board-wide AI re-score action (S-C24)."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        db = app.state.db
        kw_job = _seed(db)
        ai_job = _seed(db, url="https://ex.co/j/mode-2")
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME})
        score_entrypoint(_ctx(db, ai_job, engine=_OkEngine()))
        backfill_keyword_scores(db)
        with db.repos() as repos:
            v0 = repos.profile.get_current().version  # type: ignore[union-attr]
            assert repos.jobs.get(kw_job).score_method == "keyword"  # type: ignore[union-attr]
            assert repos.jobs.get(kw_job).scored_profile_version == v0  # type: ignore[union-attr]

        def kw_version() -> int | None:
            with db.repos() as repos:
                return repos.jobs.get(kw_job).scored_profile_version  # type: ignore[union-attr]

        # Minor edits, in either mode: nothing rescored.
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME + "\nGo."})
        assert kw_version() == v0
        client.post(
            "/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "keyword"}}
        )
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME + "\nRust."})
        assert kw_version() == v0

        # A major edit while LLM scoring is selected: Keyword rescored, AI kept.
        client.post("/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "llm"}})
        big = RESUME + "\n## Projects\n" + "- Built a streaming ledger in Rust and Go.\n" * 10
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": big})
        with db.repos() as repos:
            v_new = repos.profile.get_current().version  # type: ignore[union-attr]
            ai = repos.jobs.get(ai_job)
            assert ai is not None and ai.score_method == "llm"
        assert kw_version() == v_new > v0

        assert client.post("/api/jobs/rescore", headers=AUTH).status_code == 405


# ---------------------------------------------------------------------------
# No AI re-score action exists (S-C24): the planner's eligibility read is
# version-agnostic, so a job with an LLM score is never re-planned or re-spent.
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Any) -> Any:
    return create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )


def _score_ops_for(db: Database, job_id: str) -> int:
    """How many `score` operation rows exist for one job (any state)."""
    with db.repos() as repos:
        ops = repos.operations.list_by_kind_states("score", OP_ALL_STATES)
        return sum(
            1
            for op in ops
            if isinstance(op.input_snapshot, dict)
            and op.input_snapshot.get("job_id") == job_id
        )


def test_the_ai_rescore_routes_are_gone(tmp_path) -> None:
    """Both halves of the old re-score action 404. It was the last unbatched
    fan-out on the event loop (S-C25: 1.49 s at 1,000 jobs, against a 2 s health
    window), and the state machine replaced the recovery it existed for."""
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        a = _seed(db, url="https://ex.co/j/prev-a")
        score_entrypoint(_ctx(db, a, engine=_OkEngine()))

        assert client.get("/api/jobs/rescore/preview", headers=AUTH).status_code == 404
        # 405, not 404: with the route gone, `/api/jobs/rescore` is just
        # `/api/jobs/{job_id}`, which is registered for GET and PATCH only.
        assert client.post("/api/jobs/rescore", headers=AUTH).status_code == 405
        # And the LLM-scored job keeps the one op it earned: nothing re-spends.
        assert _score_ops_for(db, a) == 1


def test_settings_switch_to_llm_enqueues_nothing_server_side(tmp_path) -> None:
    """Switching Scoring keyword→AI is a pure settings write — the server never
    spends tokens inside the request. The next scheduler tick picks up whatever
    still has no LLM score."""
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
    """Saving the resume unchanged bumps nothing — no new version, so a save
    that changed nothing costs nothing downstream."""
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
        # AI upgrade for a job with no LLM score at the current version.
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
                company="Acme", location="Remote", description=REALISTIC_JD,
                source_adapter="greenhouse",
            ).id
        score_entrypoint(_ctx(db, a, engine=_OkEngine()))
        # Switch to keyword mode via the settings API → b gets scored inline.
        resp = client.post(
            "/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "keyword"}}
        )
        assert resp.status_code == 200
        rows = {r["id"]: r for r in client.get("/api/jobs", headers=AUTH).json()}
        assert rows[a]["score"]["scorer_impl"] == SCORER_IMPL  # LLM score kept
        assert rows[a]["score"]["score_0_100"] == 77
        assert rows[b]["score"]["scorer_impl"] == SCORER_IMPL_DETERMINISTIC


# --- too thin to score: both modes refuse, nobody pays (S-C27 / S-A6) ---------


class _ExplodingEngine:
    """Any call is a test failure: a job with no description must never reach a
    provider, in either mode."""

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        raise AssertionError("the engine was called for a job with no description")


def _seed_descriptionless(db: Database, *, url: str = "https://ex.co/j/empty") -> str:
    with db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer with Java, Python, Kafka.")
        return repos.jobs.create(
            canonical_url=url, title="Frontend Developer", company="AMINA Bank",
            location="Mumbai, Maharashtra, India (On-site)", description="",
            source_adapter="linkedin",
        ).id


def test_llm_mode_refuses_a_descriptionless_job_without_calling_the_engine(
    migrated_db: Database,
) -> None:
    """The default mode used to disagree with keyword mode in 2 bands, because
    the scorer module's own guard cuts at 80 chars and the floor cuts at 200:
    under 80 the op FAILED, and between 80 and 199 it spent tokens and returned
    a real, inflated score. Measured on the maintainer's install: 114 jobs in
    the first band, 134 in the second."""
    db = migrated_db
    job_id = _seed_descriptionless(db)
    outcome = score_entrypoint(_ctx(db, job_id, engine=_ExplodingEngine()))
    assert outcome.result_ref is not None
    assert outcome.result_ref["score"] == 0
    assert outcome.usage is None  # nothing was spent
    assert outcome.engine == "on-device"
    assert _scores(db, job_id)[SCORER_IMPL_DETERMINISTIC] == 0


def test_a_descriptionless_job_is_never_planned_for_scoring(
    migrated_db: Database,
) -> None:
    """It can never earn an LLM score, so leaving it eligible would re-plan the
    same job every tick forever, each pass writing another operation row (248
    such jobs on the maintainer's install). The predicate is on the description,
    so a later scan that fills one in makes the job eligible again on its own."""
    db = migrated_db
    empty = _seed_descriptionless(db)
    described = _seed(db, url="https://ex.co/j/described")
    planned = {snap["job_id"] for _kind, snap in plan_score_new(db)}
    assert described in planned
    assert empty not in planned


def test_a_provider_outage_costs_no_job_an_attempt(migrated_db: Database) -> None:
    """The bug this whole state exists for: a fan-out that dies on an expired
    key or exhausted tokens used to mark every remaining job `failed`, and the
    planner excluded `failed` forever, so topping the key up re-scored nothing.
    A circuit-open rejection never reached the provider, so it must not spend
    the job's budget."""
    from sidecar.app.runner.circuit import ProviderCircuitOpen
    from sidecar.app.scheduler.planner import plan_score_new

    class _CircuitOpenEngine:
        def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
            raise ProviderCircuitOpen("provider 'fake' paused after 5 failures")

    db = migrated_db
    job_id = _seed(db)
    with pytest.raises(ProviderCircuitOpen):
        score_entrypoint(_ctx(db, job_id, engine=_CircuitOpenEngine()))
    _settle_ops(db)

    with db.repos() as repos:
        assert repos.jobs.get(job_id).llm_score_attempts == 0  # type: ignore[union-attr]
    assert job_id in {snap["job_id"] for _k, snap in plan_score_new(db)}


def test_a_real_provider_failure_spends_an_attempt_and_stops_at_the_cap(
    migrated_db: Database,
) -> None:
    """A job that keeps failing against a LIVE provider must not be retried
    forever. Three attempts, then the planner stops offering it."""
    from sidecar.app.scheduler.planner import SCORE_MAX_ATTEMPTS, plan_score_new

    db = migrated_db
    job_id = _seed(db)
    for expected in range(1, SCORE_MAX_ATTEMPTS + 1):
        with pytest.raises(RuntimeError):
            score_entrypoint(_ctx(db, job_id, engine=_DeadEngine()))
        _settle_ops(db)
        with db.repos() as repos:
            job = repos.jobs.get(job_id)
            assert job is not None
            assert job.llm_score_attempts == expected
            assert "429" in (job.llm_score_last_error or "")
        planned = {snap["job_id"] for _k, snap in plan_score_new(db)}
        # Re-planned while budget remains — the old code never re-planned at all.
        assert (job_id in planned) is (expected < SCORE_MAX_ATTEMPTS)

    # Retry hands the budget back, and the job is offered again.
    with db.repos() as repos:
        assert repos.jobs.reset_score_attempts() == 1
    assert job_id in {snap["job_id"] for _k, snap in plan_score_new(db)}


def test_retry_route_counts_only_stuck_jobs_and_hands_the_budget_back(tmp_path) -> None:
    """The ledger's Retry-scoring affordance, end to end (S-C24). The count is
    what a press would actually fix — jobs that spent the WHOLE budget — so a
    job with 1 attempt left is not counted (it comes back on its own), and a
    description-less job is not counted (a retry can't help it). The POST resets
    and enqueues nothing itself: the next tick does the work, batched."""
    from sidecar.app.scheduler.planner import SCORE_MAX_ATTEMPTS

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        stuck = _seed(db, url="https://ex.co/j/stuck")
        partway = _seed(db, url="https://ex.co/j/partway")
        with db.repos() as repos:
            thin = repos.jobs.create(
                canonical_url="https://ex.co/j/thin", title="Backend Engineer",
                company="Acme", location="Remote", description="",
                source_adapter="linkedin",
            ).id
            for _ in range(SCORE_MAX_ATTEMPTS):
                repos.jobs.record_score_failure(stuck, "429 from the provider")
            repos.jobs.record_score_failure(partway, "429 from the provider")

        assert client.get("/api/scoring/retryable", headers=AUTH).json()["count"] == 1

        r = client.post("/api/scoring/retry", headers=AUTH)
        assert r.status_code == 200
        # Both jobs that spent anything get their budget back; the thin one
        # never spent one, so it is untouched and still uncounted.
        assert r.json()["reset"] == 2
        assert client.get("/api/scoring/retryable", headers=AUTH).json()["count"] == 0
        assert _score_ops_for(db, stuck) == 0  # the tick enqueues, not the route
        with db.repos() as repos:
            assert repos.jobs.get(thin).llm_score_attempts == 0  # type: ignore[union-attr]
        assert stuck in {snap["job_id"] for _k, snap in plan_score_new(db)}
        assert thin not in {snap["job_id"] for _k, snap in plan_score_new(db)}


def test_the_llm_scorer_receives_the_users_own_preferences(migrated_db: Database) -> None:
    """S-C67. The user's resume says one city and their preferences say another;
    without this the scorer only ever saw the resume, so it read the city they
    live in as the city they want (measured on the 2026-09-28 bench: unwanted
    locations scored 14.75 points HIGHER than wanted ones)."""
    db = migrated_db
    job_id = _seed(db, url="https://ex.co/j/prefs-1")
    with db.repos() as repos:
        repos.preferences.update(
            thresholds={"scoring_mode": "llm"},
            locations=["Bengaluru", "Remote"],
            role_aliases=["software engineer"],
            hard_excludes={"companies": ["Acme"], "keywords": []},
        )

    seen: dict[str, str] = {}

    class _CapturingEngine:
        def complete(self, system_prompt: str, user_prompt: str, **_: Any) -> Any:
            seen["user"] = user_prompt
            seen["system"] = system_prompt
            return SCORE_OUT, EngineUsage(internal_calls=1)

    score_entrypoint(_ctx(db, job_id, engine=_CapturingEngine()))
    assert "SEARCH PREFERENCES" in seen["user"]
    assert "Locations wanted: Bengaluru, Remote" in seen["user"]
    assert "Excluded companies: Acme" in seen["user"]
    # And the rule that tells the model what to do with the block.
    assert "Never infer a location preference from the resume's address" in seen["system"]


def test_a_user_with_no_preferences_set_sends_two_blocks(migrated_db: Database) -> None:
    db = migrated_db
    job_id = _seed(db, url="https://ex.co/j/prefs-2")
    with db.repos() as repos:
        repos.preferences.update(
            thresholds={"scoring_mode": "llm"}, locations=[], role_aliases=[]
        )

    seen: dict[str, str] = {}

    class _CapturingEngine:
        def complete(self, system_prompt: str, user_prompt: str, **_: Any) -> Any:
            seen["user"] = user_prompt
            return SCORE_OUT, EngineUsage(internal_calls=1)

    score_entrypoint(_ctx(db, job_id, engine=_CapturingEngine()))
    assert "SEARCH PREFERENCES" not in seen["user"]


def test_an_llm_score_carries_its_method_and_its_factors(migrated_db: Database) -> None:
    """4 methods write that one score slot, so the board has to be able to say
    which one did, and the bars have to come from the same call that scored."""
    db = migrated_db
    job_id = _seed(db, url="https://ex.co/j/factors-llm")
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "llm"}, locations=["Bengaluru"])

    class _Engine:
        def complete(self, system_prompt: str, user_prompt: str, **_: Any) -> Any:
            return SCORE_OUT, EngineUsage(internal_calls=1)

    score_entrypoint(_ctx(db, job_id, engine=_Engine()))
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        assert job.score == 77
        assert job.score_method == "llm"
    rows = _factors(db, job_id)
    assert set(rows) == {"core_stack", "years", "location_preference"}
    assert rows["core_stack"].value == 0.9
    assert rows["core_stack"].evidence == "Java and Python evidenced"


def test_jev_mode_scores_through_the_decisions_client(tmp_path) -> None:
    """The op opens the sealed OpenRouter key, asks once, and stores the factors
    with `method="jev"` so the board can say which method produced the number."""
    from sidecar.app.registry.engines_http import DECISIONS_PATH, DecisionsHttpClient

    class FakeTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, bytes | None]] = []

        def request(self, method, url, *, headers, body=None, timeout_s=60):
            from sidecar.app.registry.engines_http import HttpResponse

            self.calls.append((method, url, body))
            payload = {
                "answers": {
                    "hard_requirements": {"score": 4.0},
                    "core_stack": {"score": 3.0},
                    "years": {"score": 4.0},
                    "level": {"score": 4.0},
                    "nice_to_haves": {"score": 2.0},
                    "location_preference_met": {"noul": 0.95},
                    "logistics_blocker": {"noul": 0.05},
                },
                "usage": {"cost": 0.00022},
            }
            import json as _json

            return HttpResponse(status=200, body=_json.dumps(payload).encode())

    transport = FakeTransport()
    client = DecisionsHttpClient(api_key="sk-or-test", transport=transport)

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'db.sqlite'}")
    from sidecar.app.db.migrate import upgrade_to_head

    upgrade_to_head(f"sqlite:///{tmp_path / 'db.sqlite'}")
    job_id = _seed(db, url="https://ex.co/j/jev-1")
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "jev"}, locations=["Bengaluru"])

    import sidecar.app.registry.persistence as persistence

    original = persistence.load_decisions_client
    persistence.load_decisions_client = lambda _repos: client  # type: ignore[assignment]
    try:
        outcome = score_entrypoint(_ctx(db, job_id, engine=None))
    finally:
        persistence.load_decisions_client = original  # type: ignore[assignment]

    assert outcome.engine == "jev"
    assert len(transport.calls) == 1
    method, url, body = transport.calls[0]
    assert method == "POST" and url.endswith(DECISIONS_PATH)
    assert body is not None and b"candidate_resume" in body
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        assert job.score_method == "jev"
        assert job.score is not None and 0 <= job.score <= 100
        rows = {f["factor"] for f in job.score_factors}
    assert rows == {
        "hard_requirements",
        "core_stack",
        "years",
        "level",
        "nice_to_haves",
        "location_preference",
        "logistics",
    }


def test_jev_mode_without_a_key_says_so_instead_of_failing_silently(
    migrated_db: Database,
) -> None:
    from sidecar.app.registry.engines import EngineNotConfiguredError

    db = migrated_db
    job_id = _seed(db, url="https://ex.co/j/jev-2")
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "jev"})
    with pytest.raises(EngineNotConfiguredError, match="OpenRouter key"):
        score_entrypoint(_ctx(db, job_id, engine=None))


def test_the_settings_payload_lists_every_method_with_what_it_needs(tmp_path) -> None:
    """The menu never hides a method, so the server reports all 4 and says what
    each still needs; the client owns the copy and the prices (talk.md D44)."""
    app = create_app(data_dir=tmp_path, token=TOKEN, enable_scheduler=False)
    with TestClient(app) as client:
        methods = client.get("/api/settings", headers=AUTH).json()["scoring_methods"]
        assert [m["id"] for m in methods] == ["llm", "jev", "local", "keyword"]
        by_id = {m["id"]: m for m in methods}
        assert by_id["llm"]["ready"] is True and by_id["llm"]["needs"] is None
        assert by_id["keyword"]["ready"] is True
        # No OpenRouter key on a fresh install, and the pack downloader does not
        # exist yet, so 2 rows are honestly not ready.
        assert by_id["jev"] == {"id": "jev", "ready": False, "needs": "openrouter_key"}
        assert by_id["local"] == {"id": "local", "ready": False, "needs": "model_pack"}

        client.post(
            "/api/engines",
            headers=AUTH,
            json={"provider": "openrouter", "key": "sk-or-test-key", "enabled": True},
        )
        after = client.get("/api/settings", headers=AUTH).json()["scoring_methods"]
        jev = next(m for m in after if m["id"] == "jev")
        assert jev == {"id": "jev", "ready": True, "needs": None}


def test_picking_the_local_method_scores_by_keyword_and_says_so(migrated_db: Database) -> None:
    """It is on the menu because the menu hides nothing, and its pack has no
    downloader yet, so it must not silently score with something else."""
    db = migrated_db
    job_id = _seed(db, url="https://ex.co/j/local-1")
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "local"})
    outcome = score_entrypoint(_ctx(db, job_id, engine=None))
    assert outcome.engine == "on-device"
    assert outcome.result_ref is not None
    assert outcome.result_ref["fell_back_from"] == "local"
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None and job.score_method == "keyword"


# --- which method ran, on the wire -------------------------------------------
# The board's inner ring is the method, so it rides the wire per job;
# `scorer_impl` can't carry it, since it says "scorer-llm" for Jev and Laya
# too.


class _FakeDecisions:
    """The Jev transport, in the shape the 2026-09-28 bench recorded."""

    def decide(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "answers": {
                "hard_requirements": {"score": 4.0, "confidence": 0.81},
                "core_stack": {"score": 3.6, "confidence": 0.74},
                "years": {"score": 4.0, "confidence": 0.9},
                "level": {"score": 3.8, "confidence": 0.7},
                "nice_to_haves": {"score": 1.0, "confidence": 0.6},
                "location_preference_met": {"noul": 0.96},
                "logistics_blocker": {"noul": 0.04},
            },
            "usage": {"cost": 0.000215, "prompt_tokens": 5129, "completion_tokens": 132},
        }


def test_the_dto_names_the_method_that_actually_ran(migrated_db: Database) -> None:
    """LLM scoring and keyword scoring each report themselves, and the keyword
    floor under an LLM score never claims the AI's name."""
    from sidecar.app.api import dto as dto_mod

    db = migrated_db
    job_id = _seed(db)
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "keyword"})
    score_entrypoint(_ctx(db, job_id, engine=None))
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        shown = dto_mod.job_score_dto(job)
        assert shown is not None
        assert shown.method == "keyword"

    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "llm"})
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        shown = dto_mod.job_score_dto(job)
        assert shown is not None
        # The LLM score replaced the keyword floor, and it says AI.
        assert job.score_method == "llm"
        assert shown.method == "llm"


def test_a_jev_score_says_jev_and_not_llm(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this pins: Jev scoring once wrote `jobs.llm_score`, so every
    AI-or-not test in the app reads it as an LLM score. Only `method` can tell
    the user they are looking at a $0.0002 number rather than a $0.24 one."""
    from sidecar.app.api import dto as dto_mod
    from sidecar.app.registry import persistence as persistence_mod

    db = migrated_db
    job_id = _seed(db)
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "jev"})
    monkeypatch.setattr(
        persistence_mod, "load_decisions_client", lambda repos: _FakeDecisions()
    )
    outcome = score_entrypoint(_ctx(db, job_id, engine=None))

    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        assert job.score is not None
        shown = dto_mod.job_score_dto(job)
        assert shown is not None
        assert shown.method == "jev"
        # `scorer_impl` is the field that cannot tell them apart. Pinned so
        # nobody "fixes" the ring by reading it again.
        assert shown.scorer_impl == SCORER_IMPL

    # And the ledger says it too, so the Analytics row and the board agree.
    assert outcome.result_ref is not None
    assert outcome.result_ref["method"] == "jev"


def test_laya_falling_back_to_keyword_says_both(migrated_db: Database) -> None:
    """Laya has no model pack yet (scoring-build.md T10), so it runs the
    keyword floor. The ledger says keyword ran AND that Laya was asked for,
    because a user who picked a method deserves to know it didn't run."""
    db = migrated_db
    job_id = _seed(db)
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "local"})
    outcome = score_entrypoint(_ctx(db, job_id, engine=None))
    assert outcome.result_ref is not None
    assert outcome.result_ref["method"] == "keyword"
    assert outcome.result_ref["fell_back_from"] == "local"


# --- "explain this one" (scoring-build.md T13) --------------------------------


def test_explain_enqueues_an_ordinary_score_op_with_the_mode_named(tmp_path) -> None:
    """1 click, 1 job, 1 AI call. It is a `score` operation and not a new kind,
    so cost, the LLM cap, Stop and Retry all apply without being re-built."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        db = app.state.db
        job_id = _seed(db)
        with db.repos() as repos:
            repos.preferences.update(thresholds={"scoring_mode": "keyword"})

        resp = client.post(f"/api/jobs/{job_id}/explain", headers=AUTH)
        assert resp.status_code == 202
        body = resp.json()
        assert body["kind"] == "score"

        with db.repos() as repos:
            op = repos.operations.get(body["id"])
            assert op is not None
            # The mode rides the snapshot. Nothing else in the app sets it, so a
            # scan can never spend on a mode the user did not pick.
            assert op.input_snapshot["force_mode"] == "llm"
            assert op.input_snapshot["job_id"] == job_id


def test_explain_runs_the_ai_path_even_while_the_board_is_on_keyword(
    migrated_db: Database,
) -> None:
    db = migrated_db
    job_id = _seed(db)
    with db.repos() as repos:
        repos.preferences.update(thresholds={"scoring_mode": "keyword"})
    ctx = _ctx(db, job_id, engine=_OkEngine())
    with db.repos() as repos:
        snap = dict(ctx.input_snapshot)
        snap["force_mode"] = "llm"
    forced = OperationContext(
        kind="score", input_snapshot=snap, engine=ctx.engine, db=db,
        operation_id=ctx.operation_id,
    )
    outcome = score_entrypoint(forced)
    assert outcome.result_ref is not None
    assert outcome.result_ref["method"] == "llm"
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        # It ADOPTS the score it paid for, and the row says which method ran.
        assert job.score == 77
        assert job.score_method == "llm"


def test_explain_refuses_a_posting_with_too_little_to_explain(tmp_path) -> None:
    """Spending the user's money to say "we know nothing about this job" is the
    exact defect MIN_JD_CHARS exists to stop (S-C27)."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        db = app.state.db
        with db.repos() as repos:
            repos.profile.upsert("# Master\n\nBackend engineer.")
            job_id = repos.jobs.create(
                canonical_url="https://ex.co/j/explain-thin",
                title="Backend Engineer",
                description="Backend engineer.",
                source_adapter="greenhouse",
            ).id
        resp = client.post(f"/api/jobs/{job_id}/explain", headers=AUTH)
        assert resp.status_code == 400
        with db.repos() as repos:
            assert repos.operations.list_by_kind_states("score", OP_ALL_STATES) == []


def test_explain_refuses_with_no_master_resume(tmp_path) -> None:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        db = app.state.db
        with db.repos() as repos:
            job_id = repos.jobs.create(
                canonical_url="https://ex.co/j/explain-noresume",
                title="Backend Engineer",
                description=REALISTIC_JD,
                source_adapter="greenhouse",
            ).id
        assert client.post(f"/api/jobs/{job_id}/explain", headers=AUTH).status_code == 400
