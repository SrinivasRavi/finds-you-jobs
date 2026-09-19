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
    """Every scorer's rating on this job, keyed by impl — the columns read back
    under the enum names the rest of the suite already speaks."""
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        out = {}
        if job.llm_score is not None:
            out[SCORER_IMPL] = job.llm_score
        if job.keyword_score is not None:
            out[SCORER_IMPL_DETERMINISTIC] = job.keyword_score
        return out


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


def test_llm_planner_upgrades_keyword_floored_jobs_and_retries_failures(
    migrated_db: Database,
) -> None:
    """AI mode: a keyword-floored job is still planned for an LLM upgrade (the
    floor doesn't count as done), and a job whose LLM attempt failed IS planned
    again while its attempt budget lasts.

    That second half used to be the opposite. A `failed` score op excluded the
    job from every future tick, so a fan-out that died on an expired key left
    every job in it permanently unscored. The bound is `Job.score_attempts` now
    (see `test_a_real_provider_failure_spends_an_attempt_and_stops_at_the_cap`)."""
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
    score_entrypoint(_ctx(db, a, engine=_OkEngine()))  # a: real AI score
    assert backfill_keyword_scores(db) == 1  # only b
    assert SCORER_IMPL_DETERMINISTIC not in _scores(db, a)
    assert SCORER_IMPL_DETERMINISTIC in _scores(db, b)


def test_ai_score_survives_a_resume_edit_and_its_keyword_sweep(
    migrated_db: Database,
) -> None:
    """A resume edit never blanks the board, and the keyword sweep it fires can
    never bury the AI score. The 2 ratings are separate columns and the display
    rule prefers the AI one outright, so the defect that showed 26 where the AI
    had said 82 on 36 jobs is not expressible any more (S-C34)."""
    from sidecar.app.api import dto as dto_mod

    db = migrated_db
    job_id = _seed(db)
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        ai_score = job.llm_score
        assert ai_score == 77
        assert dto_mod.job_score_dto(job).scorer_impl == SCORER_IMPL  # type: ignore[union-attr]
        repos.profile.upsert("# Test Candidate\n\nBackend engineer. Now with Go.")
    # The sweep writes a keyword rating into its own column. The AI one shows.
    rescore_all_keyword(db)
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        assert job.keyword_score is not None
        shown = dto_mod.job_score_dto(job)
        assert shown is not None
        assert shown.scorer_impl == SCORER_IMPL
        assert shown.score_0_100 == ai_score


def test_llm_planner_does_not_auto_rescore_after_a_resume_edit(
    migrated_db: Database,
) -> None:
    """AI mode: a job already AI-scored at any version is not re-planned after a
    resume edit. An AI score is version-agnostic (D1), so the prior score stays
    on the board and no tick ever re-spends on it."""
    db = migrated_db
    job_id = _seed(db)
    score_entrypoint(_ctx(db, job_id, engine=_OkEngine()))
    with db.repos() as repos:
        repos.profile.upsert("# Test Candidate\n\nBackend engineer. Kafka, Go.")
    assert plan_score_new(db) == []  # no auto re-score at the new version


def test_api_resume_edit_keyword_mode_auto_rescores_llm_mode_does_not(tmp_path) -> None:
    """Through the real app: editing the resume re-scores the whole board for
    free in keyword mode, and leaves prior scores untouched in AI mode. There
    is no AI re-score action at all (S-C24, maintainer 2026-08-28)."""
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
        # (No score existed here, so it simply stays unscored — the point is
        # that no keyword re-score is forced.)
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME + "\nGo."})
        with db.repos() as repos:
            job = repos.jobs.get(job_id)
            assert job is not None
            assert job.llm_score is None and job.keyword_score is None

        # Switch to keyword mode, edit again → the board is re-scored for free.
        client.post(
            "/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "keyword"}}
        )
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME + "\nRust."})
        with db.repos() as repos:
            v_new = repos.profile.get_current().version  # type: ignore[union-attr]
            job = repos.jobs.get(job_id)
            assert job is not None
            assert v_new > v0
            assert job.keyword_score is not None and job.llm_score is None

        # Back in AI mode, the route that used to re-score the board is gone
        # (405: the path now resolves to GET/PATCH `/api/jobs/{job_id}`).
        client.post("/api/settings", headers=AUTH, json={"thresholds": {"scoring_mode": "llm"}})
        assert client.post("/api/jobs/rescore", headers=AUTH).status_code == 405


# ---------------------------------------------------------------------------
# No AI re-score action exists (maintainer 2026-08-28, S-C24). A score is a
# cache row keyed by (job, profile_version, scorer_impl), and the planner's
# eligibility read is version-agnostic, so a job that already carries an AI
# score is never re-planned and never re-spent, whatever changes upstream.
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
        # And the AI-scored job keeps the one op it earned: nothing re-spends.
        assert _score_ops_for(db, a) == 1


def test_settings_switch_to_llm_enqueues_nothing_server_side(tmp_path) -> None:
    """Switching Scoring keyword→AI is a pure settings write — the server never
    spends tokens inside the request. The next scheduler tick picks up whatever
    still has no AI score."""
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
        assert rows[a]["score"]["scorer_impl"] == SCORER_IMPL  # AI score kept
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
