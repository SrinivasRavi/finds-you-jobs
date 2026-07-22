"""Deterministic-scoring experiment: boot backfill.

The maintainer checked the branch out over an existing profile and saw no
deterministic scores anywhere (2026-07-22) — they are computed inside the
score operation, so jobs scored BEFORE the branch existed never get one.
Boot must backfill them: second app boot on the same data dir fills the
zero-LLM row for every already-scored job and the board serves it.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.registry.persistence import SCORER_IMPL

TOKEN = "test-token-det-backfill"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}

RESUME = "# Test Candidate\n\nBackend engineer. Python, FastAPI, SQL, AWS.\n"
JD = (
    "Senior Backend Engineer. 5+ years Python and FastAPI. SQL and AWS "
    "experience required. Remote."
)


def _make_app(tmp_path: Path):
    return create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )


def test_boot_backfills_deterministic_scores_for_previously_scored_jobs(
    tmp_path: Path,
) -> None:
    # Boot 1: an onboarded profile and a job scored by the LLM path only —
    # the exact state of a board that predates the experiment branch.
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": RESUME})
        with app.state.db.repos() as repos:
            profile = repos.profile.get_current()
            assert profile is not None
            job = repos.jobs.create(
                canonical_url="https://boards.greenhouse.io/detco/jobs/1",
                title="Senior Backend Engineer", company="DetCo",
                location="Remote", description=JD, source_adapter="greenhouse",
            )
            repos.job_scores.upsert(
                job_id=job.id,
                profile_version=int(profile.version),
                score_0_100=80,
                reasons=["llm reason"],
                breakdown_md="llm breakdown",
                scorer_impl=SCORER_IMPL,
            )
        # No deterministic row yet — the pre-branch state.
        rows = client.get("/api/jobs", headers=AUTH).json()
        assert rows and rows[0]["deterministicScore"] is None

    # Boot 2 (same data dir): the backfill thread must fill the gap.
    app2 = _make_app(tmp_path)
    with TestClient(app2) as client:
        deadline = time.monotonic() + 10
        det = None
        while time.monotonic() < deadline:
            rows = client.get("/api/jobs", headers=AUTH).json()
            det = rows[0]["deterministicScore"] if rows else None
            if det is not None:
                break
            time.sleep(0.1)
        assert det is not None, "boot backfill never produced a deterministic score"
        assert 0 <= det["score_0_100"] <= 100
        assert det["scorer_impl"] == "scorer-deterministic"
        # The real score is untouched.
        assert rows[0]["score"]["score_0_100"] == 80
        assert rows[0]["score"]["scorer_impl"] == SCORER_IMPL
