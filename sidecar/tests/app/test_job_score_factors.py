"""1 score per job: its method, its factors and the resume it predates (audit A3).

`docs/internal/score-storage.md` section 4: a job keeps only its latest score and
the factors that made it; a Keyword write never replaces a real score unless the
user picked Rescore; a score made before a major resume edit is marked stale, and
nothing rescores on its own. The factor bars and the weight lens read the row.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from sidecar.app.api import dto
from sidecar.app.db import Database
from sidecar.app.main import create_app
from sidecar.app.scheduler.planner import plan_score_new

RESUME = "# Asha\n## Experience\n**Acme** | Engineer\n- Built Python services.\n"


def _job(db: Database, url: str) -> str:
    with db.repos() as repos:
        return repos.jobs.create(
            canonical_url=url, title="T", source_adapter="lever", description="x" * 400
        ).id


def _score(db: Database, job_id: str, method: str, value: int, factors: list[dict],
           **extra: Any) -> bool:
    with db.repos() as repos:
        return repos.jobs.set_score(
            job_id,
            scorer_impl="scorer-deterministic" if method == "keyword" else "scorer-llm",
            score_0_100=value,
            reasons=[method],
            breakdown_md="",
            method=method,
            factors=factors,
            **extra,
        )


def _row(db: Database, job_id: str) -> tuple[int | None, str | None, list[dict]]:
    with db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        return job.score, job.score_method, list(job.score_factors)


def test_a_rescore_replaces_the_factor_set_rather_than_merging(migrated_db: Database) -> None:
    """A method that stops emitting a factor must stop having one, or the chart
    shows an axis the current rubric no longer scores."""
    job_id = _job(migrated_db, "u-factors-1")
    _score(migrated_db, job_id, "keyword", 40, [
        {"factor": "location_preference", "value": 1.0, "evidence": "Bengaluru"},
        {"factor": "years", "value": 0.6},
    ])
    _score(migrated_db, job_id, "keyword", 30, [{"factor": "years", "value": 0.25}])
    _, _, factors = _row(migrated_db, job_id)
    assert [(f["factor"], f["value"]) for f in factors] == [("years", 0.25)]


def test_a_job_keeps_only_the_latest_methods_score_and_factors(migrated_db: Database) -> None:
    """Keyword, then Jev, then LLM: 1 score and 1 factor set, the LLM one's."""
    job_id = _job(migrated_db, "u-factors-2")
    _score(migrated_db, job_id, "keyword", 40, [{"factor": "years", "value": 0.5}])
    _score(migrated_db, job_id, "jev", 61, [{"factor": "years", "value": 0.9,
                                             "kind": "probability"}])
    _score(migrated_db, job_id, "llm", 77, [{"factor": "core_stack", "value": 0.8}])
    score, method, factors = _row(migrated_db, job_id)
    assert (score, method) == (77, "llm")
    assert [f["factor"] for f in factors] == ["core_stack"]


def test_a_keyword_write_never_replaces_a_real_score_unless_the_user_asked(
    migrated_db: Database,
) -> None:
    job_id = _job(migrated_db, "u-factors-3")
    _score(migrated_db, job_id, "jev", 61, [])
    assert _score(migrated_db, job_id, "keyword", 12, []) is False
    assert _row(migrated_db, job_id)[:2] == (61, "jev")
    _score(migrated_db, job_id, "keyword", 12, [], rescore=True)
    assert _row(migrated_db, job_id)[:2] == (12, "keyword")


def test_a_keyword_write_loses_to_a_real_score_that_lands_after_its_read(
    migrated_db: Database,
) -> None:
    """Audit A26: the keyword floor read the job as unscored, an LLM score committed,
    then the floor's write replaced it (CI, PR #11)."""
    job_id = _job(migrated_db, "u-factors-race-1")
    with migrated_db.repos() as floor:
        seen = floor.jobs.get(job_id)  # held, as a session that loaded it would
        assert seen is not None and seen.score_method is None
        _score(migrated_db, job_id, "llm", 77, [])
        assert floor.jobs.set_score(
            job_id, scorer_impl="scorer-deterministic", score_0_100=12,
            reasons=[], breakdown_md="", method="keyword",
        ) is False
    assert _row(migrated_db, job_id)[:2] == (77, "llm")


def test_a_keyword_write_from_an_older_resume_never_replaces_a_newer_one(
    migrated_db: Database,
) -> None:
    """Audit A26: a sweep that read resume version 1 wrote after a version 2 rescore."""
    job_id = _job(migrated_db, "u-factors-race-2")
    _score(migrated_db, job_id, "keyword", 40, [], profile_version=2)
    assert _score(migrated_db, job_id, "keyword", 12, [], profile_version=1) is False
    assert _row(migrated_db, job_id)[:2] == (40, "keyword")
    _score(migrated_db, job_id, "keyword", 12, [], profile_version=1, rescore=True)
    assert _row(migrated_db, job_id)[:2] == (12, "keyword")


def test_a_factor_carries_its_kind_and_empty_evidence_by_default(
    migrated_db: Database,
) -> None:
    job_id = _job(migrated_db, "u-factors-4")
    _score(migrated_db, job_id, "keyword", 5, [{"factor": "level", "value": 0.0}],
           profile_version=3)
    with migrated_db.repos() as repos:
        job = repos.jobs.get(job_id)
        assert job is not None
        assert job.score_factors == [
            {"factor": "level", "value": 0.0, "kind": "graded", "evidence": ""}
        ]
        assert job.scored_at is not None
        assert job.scored_profile_version == 3


def test_the_planner_leaves_a_real_score_alone_and_upgrades_a_keyword_one(
    migrated_db: Database,
) -> None:
    """SCO-D10 under the 2026-10-13 rule: a Jev score stays when the setting moves
    to LLM scoring; a Keyword score is upgraded."""
    with migrated_db.repos() as repos:
        repos.profile.upsert(RESUME)
        repos.preferences.update(thresholds={"scoring_mode": "llm"})
    fast = _job(migrated_db, "u-factors-fast")
    floor = _job(migrated_db, "u-factors-floor")
    _score(migrated_db, fast, "jev", 61, [])
    _score(migrated_db, floor, "keyword", 30, [])
    assert {snap["job_id"] for _kind, snap in plan_score_new(migrated_db)} == {floor}


def test_a_major_resume_edit_marks_older_scores_stale_and_a_minor_one_does_not(
    migrated_db: Database,
) -> None:
    job_id = _job(migrated_db, "u-factors-stale")
    with migrated_db.repos() as repos:
        version = repos.profile.upsert(RESUME).version
    _score(migrated_db, job_id, "llm", 77, [], profile_version=version)

    def stale() -> bool:
        with migrated_db.repos() as repos:
            job = repos.jobs.get(job_id)
            assert job is not None
            lens = dto.build_weight_lens(repos, [job_id])
            shown = dto.job_dto(job, lens=lens).score
            assert shown is not None
            return shown.stale

    with migrated_db.repos() as repos:
        repos.profile.upsert(RESUME.replace("Built", "Led and built"))
    assert stale() is False
    with migrated_db.repos() as repos:
        repos.profile.upsert(RESUME + "## Projects\n- **Ledger**: a ledger in Rust.\n")
    assert stale() is False  # a new section, but 40 characters: minor
    with migrated_db.repos() as repos:
        repos.profile.upsert(RESUME + "## Projects\n" + "- Shipped a ledger in Rust.\n" * 12)
    assert stale() is True
    # The score stays; nothing rescored it.
    assert _row(migrated_db, job_id)[:2] == (77, "llm")


# --- the routes -----------------------------------------------------------------

TOKEN = "test-token-factors-route"  # noqa: S105 — test fixture
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _app(tmp_path: Any) -> Any:
    return create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )


def test_the_factors_route_serves_the_axes_and_the_table_they_were_weighted_with(
    tmp_path,
) -> None:
    """No model call, and the weight table the method used."""
    app = _app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        job_id = _job(db, "https://ex.co/j/factors-route")
        _score(db, job_id, "jev", 74, [
            {"factor": "core_stack", "value": 0.9, "kind": "graded",
             "evidence": "Python evidenced"},
            {"factor": "location_preference", "value": 0.96, "kind": "probability",
             "evidence": "96% confident"},
        ])
        body = client.get(f"/api/jobs/{job_id}/factors", headers=AUTH).json()
        assert body["method"] == "jev"
        assert {f["factor"] for f in body["factors"]} == {"core_stack", "location_preference"}
        kinds = {f["factor"]: f["kind"] for f in body["factors"]}
        assert kinds["location_preference"] == "probability"
        # The table Jev's own total used, so a bar's weight is the real one.
        assert body["weights"]["core_stack"] == 0.30
        # A confidence axis modifies the total; it is not weighted into it.
        assert "location_preference" not in body["weights"]


def test_llm_scoring_serves_no_weights_because_it_weights_nothing(tmp_path) -> None:
    """LLM scoring asks a model for the total directly, so there is no weight per
    axis; inventing one would be a number nobody computed."""
    app = _app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        job_id = _job(db, "https://ex.co/j/factors-llm")
        _score(db, job_id, "llm", 82, [{"factor": "core_stack", "value": 0.9}])
        body = client.get(f"/api/jobs/{job_id}/factors", headers=AUTH).json()
        assert body["method"] == "llm"
        assert body["weights"] == {}


def test_rescore_always_queues_the_users_score_with_the_method_in_settings(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        db = app.state.db
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME})
        job_id = _job(db, "https://ex.co/j/rescore")
        _score(db, job_id, "llm", 82, [])
        r = client.post(f"/api/jobs/{job_id}/rescore", headers=AUTH)
        assert r.status_code == 202
        with db.repos() as repos:
            op = repos.operations.get(r.json()["id"])
            assert op is not None
            assert op.kind == "score" and op.requested_by == "user"
            assert op.input_snapshot["rescore"] is True
            assert "force_mode" not in op.input_snapshot
        assert client.post("/api/jobs/nope/rescore", headers=AUTH).status_code == 404


def test_a_keyword_score_stores_its_lines_as_keys_and_facts() -> None:
    """Audit section 5: each reason is `{key, args, text}` and each factor carries its
    code, so the screen words them in the user's language; the English stays as `text`."""
    from sidecar.app.registry.operations import _factor_rows, _stored_reasons
    from sidecar.modules.geo.resolve import resolve_wanted
    from sidecar.modules.scorer.deterministic import score_deterministic

    wanted = resolve_wanted(["Pune"])
    det = score_deterministic(
        "# Asha\n\nSenior backend engineer. Python, SQL, AWS. 6 years.",
        "Senior backend engineer. Python, PostgreSQL, AWS. 5+ years required. " * 4,
        title="Senior Backend Engineer", location="Mumbai, India", wanted=wanted,
    )
    stored = _stored_reasons(det)
    assert [r["key"] for r in stored] == [
        "kw.skills", "kw.years", "kw.level", "kw.location", "kw.penalty",
    ]
    assert stored[0]["text"] == det.reasons[0]
    assert stored[3]["args"]["why"]["key"] == "loc.elsewhere"
    codes = {row["factor"]: row["code"] for row in _factor_rows(det)}
    assert codes["core_stack"]["key"] == "ev.skills"
    assert codes["location_preference"]["key"] == "loc.elsewhere"
