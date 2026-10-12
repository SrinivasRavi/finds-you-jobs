"""User axis weights (scoring.md 7.4).

The 2 rules that make this safe, and both are tested here rather than argued:

- A weight change costs nothing and spends nothing. The factors are already
  stored, so the total is arithmetic over rows we have.
- A weight change is never written over the score. An AI score is the model's
  own number, and "balanced" has to be able to return to it, so the re-total
  happens on read and the stored column is untouched.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.main import create_app

TOKEN = "test-token-score-weights"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# One job, scored 50 by its method, whose axes disagree sharply: the stack is
# strong and the seniority is weak. So "skills only" must raise it and
# "seniority first" must lower it, from the same stored rows.
FACTORS = [
    {"factor": "hard_requirements", "value": 0.5, "kind": "graded", "evidence": ""},
    {"factor": "core_stack", "value": 1.0, "kind": "graded", "evidence": ""},
    {"factor": "years", "value": 0.5, "kind": "graded", "evidence": ""},
    {"factor": "level", "value": 0.0, "kind": "graded", "evidence": ""},
    {"factor": "nice_to_haves", "value": 0.5, "kind": "graded", "evidence": ""},
]


def _seed(db: Database, *, method: str = "jev", score: int = 50) -> str:
    with db.repos() as repos:
        job_id = repos.jobs.create(
            canonical_url=f"https://ex.co/j/weights-{method}",
            title="Backend Engineer",
            company="Acme",
            location="Remote",
            description="x" * 400,
            source_adapter="greenhouse",
        ).id
        repos.jobs.set_score(
            job_id,
            scorer_impl="scorer-llm",
            score_0_100=score,
            reasons=["because"],
            breakdown_md="| a | b |",
            method=method,
        )
        repos.job_score_factors.replace(job_id, method, FACTORS)
    return job_id


def _client(tmp_path: Any) -> tuple[TestClient, Any]:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    return TestClient(app), app


def _row(client: TestClient, job_id: str) -> dict[str, Any]:
    return client.get(f"/api/jobs/{job_id}", headers=AUTH).json()


def test_an_untouched_install_sees_exactly_the_number_its_method_produced(tmp_path) -> None:
    """Assumption A7: an update must not reshuffle somebody's board. `balanced`
    is an empty table, so the lens is inert and the stored score is served."""
    client, app = _client(tmp_path)
    with client:
        job_id = _seed(app.state.db)
        row = _row(client, job_id)
        assert row["score"]["score_0_100"] == 50
        assert row["score"]["weighted"] is False


def test_a_preset_re_totals_the_same_stored_factors_in_both_directions(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        job_id = _seed(app.state.db)

        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "skills_only"}},
        )
        skills = _row(client, job_id)["score"]
        # 0.45*0.5 + 0.55*1.0 over a scale of 1.0 → 78.
        assert skills["score_0_100"] == 78
        assert skills["weighted"] is True

        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "seniority_first"}},
        )
        seniority = _row(client, job_id)["score"]
        assert seniority["score_0_100"] < 50
        assert seniority["weighted"] is True


def test_balanced_returns_to_the_model_s_own_number(tmp_path) -> None:
    """The reason this is computed on read. An AI score is not a weighted sum of
    anything, so a re-weighted total written over it would be unrecoverable."""
    client, app = _client(tmp_path)
    with client:
        db = app.state.db
        job_id = _seed(db, method="llm", score=50)
        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "skills_only"}},
        )
        assert _row(client, job_id)["score"]["score_0_100"] == 78
        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "balanced"}},
        )
        assert _row(client, job_id)["score"]["score_0_100"] == 50
        # And the column was never touched, which is what makes that possible.
        with db.repos() as repos:
            job = repos.jobs.get(job_id)
            assert job is not None
            assert job.llm_score == 50


def test_custom_slider_values_outrank_the_preset(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        job_id = _seed(app.state.db)
        client.post(
            "/api/settings", headers=AUTH,
            json={
                "thresholds": {
                    "score_weight_preset": "seniority_first",
                    "score_weights": {"core_stack": 1.0},
                }
            },
        )
        # Only `core_stack` carries weight, and it is 1.0, so the job is a 100.
        assert _row(client, job_id)["score"]["score_0_100"] == 100


def test_a_job_whose_method_recorded_no_factors_keeps_its_stored_score(tmp_path) -> None:
    """There is nothing to weight, so the stored number stands rather than
    collapsing to a 0 the user would read as a bad job."""
    client, app = _client(tmp_path)
    with client:
        db = app.state.db
        with db.repos() as repos:
            job_id = repos.jobs.create(
                canonical_url="https://ex.co/j/weights-bare",
                title="Backend Engineer",
                source_adapter="greenhouse",
                description="x" * 400,
            ).id
            repos.jobs.set_score(
                job_id,
                scorer_impl="scorer-llm",
                score_0_100=64,
                reasons=[],
                breakdown_md="",
                method="llm",
            )
        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "skills_only"}},
        )
        row = _row(client, job_id)["score"]
        assert row["score_0_100"] == 64
        assert row["weighted"] is False


def test_a_confidence_axis_is_never_multiplied_by_a_weight(tmp_path) -> None:
    """talk.md D43: a probability times a user weight is a number with no
    meaning. The location axis still moves the total, through its own penalty."""
    from sidecar.modules.scorer.types import Factor
    from sidecar.modules.scorer.weights import user_total

    graded = [Factor("core_stack", 1.0, "graded", "")]
    confidence = Factor("location_preference", 0.1, "probability", "")
    table = {"core_stack": 0.5, "location_preference": 0.5}

    # The confidence contributes nothing to the weighted sum...
    assert user_total(graded + [confidence], table, location_penalty=0) == 100
    # ...and everything to the penalty, which is where location belongs.
    assert user_total(graded + [confidence], table, location_penalty=30) == 70


def _seed_with_location_miss(db: Database, *, method: str, score: int) -> str:
    """The FACTORS job, plus a location the user did not ask for."""
    job_id = _seed(db, method=method, score=score)
    with db.repos() as repos:
        repos.job_score_factors.replace(
            job_id,
            method,
            [
                *FACTORS,
                {"factor": "location_preference", "value": 0.0, "kind": "probability",
                 "evidence": ""},
            ],
        )
    return job_id


def test_location_first_deepens_the_miss_penalty_on_the_method_s_own_table(tmp_path) -> None:
    """`location_first` carries no weight table, only a 35-point penalty, so it
    has to re-total on the method's own table. An empty table used to read as
    "nothing to do" and the preset changed no number at all."""
    client, app = _client(tmp_path)
    with client:
        # JEV_WEIGHTS over FACTORS is 57.5; the shipped 15-point miss makes 42.
        job_id = _seed_with_location_miss(app.state.db, method="jev", score=42)
        assert _row(client, job_id)["score"]["score_0_100"] == 42
        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "location_first"}},
        )
        row = _row(client, job_id)["score"]
        assert row["score_0_100"] == 22  # 57.5 - 35
        assert row["weighted"] is True


def test_the_penalty_slider_alone_moves_a_balanced_board(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        job_id = _seed_with_location_miss(app.state.db, method="jev", score=42)
        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_location_penalty": 0}},
        )
        from sidecar.modules.scorer.types import Factor
        from sidecar.modules.scorer.weights import JEV_WEIGHTS, user_total

        no_miss = user_total([Factor(**f) for f in FACTORS], JEV_WEIGHTS)
        assert no_miss is not None and no_miss > 42
        assert _row(client, job_id)["score"]["score_0_100"] == no_miss


def test_location_first_leaves_an_ai_score_alone(tmp_path) -> None:
    """AI scoring weighs location inside the model's own number and ships no
    axis table, so there is nothing for a penalty-only preset to re-total."""
    client, app = _client(tmp_path)
    with client:
        job_id = _seed_with_location_miss(app.state.db, method="llm", score=42)
        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "location_first"}},
        )
        row = _row(client, job_id)["score"]
        assert row["score_0_100"] == 42
        assert row["weighted"] is False


def test_re_weighting_never_lifts_a_keyword_score_past_its_red_flag_cap(tmp_path) -> None:
    """The keyword scorer clamps a red-flag posting to 40 as a scam-risk floor.
    The factors carry no trace of that clamp, so a re-total would quietly undo
    it: "skills only" turned a capped 40 into a 100."""
    client, app = _client(tmp_path)
    with client:
        db = app.state.db
        with db.repos() as repos:
            job_id = repos.jobs.create(
                canonical_url="https://ex.co/j/weights-red-flag",
                title="Backend Engineer",
                company="Acme",
                description="x" * 400,
                source_adapter="greenhouse",
                trust_flags=["red-flag-term:pay to apply"],
            ).id
            repos.jobs.set_score(
                job_id,
                scorer_impl="scorer-deterministic",
                score_0_100=40,
                reasons=[],
                breakdown_md="",
                method="keyword",
                factors=[
                    {"factor": "core_stack", "value": 1.0, "kind": "graded", "evidence": ""},
                    {"factor": "years", "value": 1.0, "kind": "graded", "evidence": ""},
                ],
            )
        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "skills_only"}},
        )
        assert _row(client, job_id)["score"]["score_0_100"] == 40


def test_the_board_ranks_by_the_number_it_shows_across_pages(tmp_path) -> None:
    """The board sorts before it paginates. Sorting on the stored score while
    showing the re-weighted one put a 100 on page 2 behind a 50 on page 1."""
    client, app = _client(tmp_path)
    with client:
        db = app.state.db
        weak = _seed(db, method="jev", score=60)  # FACTORS: weak level, strong stack
        with db.repos() as repos:
            strong = repos.jobs.create(
                canonical_url="https://ex.co/j/weights-strong",
                title="Backend Engineer",
                company="Acme",
                description="x" * 400,
                source_adapter="greenhouse",
            ).id
            repos.jobs.set_score(
                strong, scorer_impl="scorer-llm", score_0_100=50, reasons=[],
                breakdown_md="", method="jev",
            )
            repos.job_score_factors.replace(
                strong, "jev",
                [{"factor": "core_stack", "value": 1.0, "kind": "graded", "evidence": ""},
                 {"factor": "hard_requirements", "value": 1.0, "kind": "graded",
                  "evidence": ""}],
            )
        client.post(
            "/api/settings", headers=AUTH,
            json={"thresholds": {"score_weight_preset": "skills_only"}},
        )
        first = client.get("/api/board?page=0&page_size=1", headers=AUTH).json()["jobs"]
        assert [j["id"] for j in first] == [strong]
        assert first[0]["score"]["score_0_100"] == 100
        second = client.get("/api/board?page=1&page_size=1", headers=AUTH).json()["jobs"]
        assert [j["id"] for j in second] == [weak]


def test_the_bars_and_the_lens_use_the_weights_the_keyword_scorer_used(tmp_path) -> None:
    """Audit finding 7: with no seniority in the title the keyword scorer
    weighs skills and years 60/40. The bars showed 55/30, and the weight lens
    renormalized 55/30 to 64.7/35.3, so 3 places told 3 different stories."""
    from sidecar.modules.scorer.types import Factor
    from sidecar.modules.scorer.weights import method_weights, user_total

    no_level = {"core_stack", "years"}
    assert dict(method_weights("keyword", no_level)) == {"core_stack": 0.60, "years": 0.40}
    assert method_weights("keyword", no_level | {"level"})["level"] == 0.15
    factors = [Factor("core_stack", 0.5), Factor("years", 1.0)]
    # 0.6 * 50 + 0.4 * 100 = 70, the scorer's own arithmetic.
    assert user_total(factors, method_weights("keyword", no_level)) == 70
