"""Per-axis score detail: one row per (job, method, factor), replaced on rescore.

The table exists so the factor bars, a user weight and a "what failed on
location" query read one shape whichever scorer ran (`scoring.md` section 7.2).
These tests pin the 3 rules that make that true: a rescore replaces rather than
merges, 2 methods coexist on 1 job, and a value carries what kind of value it is.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from sidecar.app.db import Database


def _job(db: Database, url: str) -> str:
    with db.repos() as repos:
        return repos.jobs.create(canonical_url=url, title="T", source_adapter="lever").id


def test_a_rescore_replaces_the_methods_rows_rather_than_merging(migrated_db: Database) -> None:
    """A method that stops emitting a factor must stop having one, or the chart
    shows an axis the current rubric no longer scores."""
    job_id = _job(migrated_db, "u-factors-1")
    with migrated_db.repos() as repos:
        repos.job_score_factors.replace(
            job_id,
            "keyword",
            [
                {"factor": "location_preference", "value": 1.0, "evidence": "Bengaluru"},
                {"factor": "years", "value": 0.6},
            ],
        )
    with migrated_db.repos() as repos:
        repos.job_score_factors.replace(
            job_id, "keyword", [{"factor": "years", "value": 0.25}]
        )
    with migrated_db.repos() as repos:
        rows = repos.job_score_factors.list_for_job(job_id)
        assert [(r.factor, r.value) for r in rows] == [("years", 0.25)]


def test_two_methods_hold_their_own_factors_for_one_job(migrated_db: Database) -> None:
    job_id = _job(migrated_db, "u-factors-2")
    with migrated_db.repos() as repos:
        repos.job_score_factors.replace(
            job_id, "keyword", [{"factor": "years", "value": 0.5}]
        )
        repos.job_score_factors.replace(
            job_id,
            "jev",
            [{"factor": "years", "value": 0.9, "kind": "probability"}],
        )
    with migrated_db.repos() as repos:
        assert len(repos.job_score_factors.list_for_job(job_id)) == 2
        keyword = repos.job_score_factors.list_for_job(job_id, "keyword")
        assert [(r.value, r.kind) for r in keyword] == [(0.5, "graded")]
        jev = repos.job_score_factors.list_for_job(job_id, "jev")
        assert [(r.value, r.kind) for r in jev] == [(0.9, "probability")]


def test_one_query_reads_a_page_of_jobs(migrated_db: Database) -> None:
    """The board must never ask per card (the F-H2 lesson)."""
    ids = [_job(migrated_db, f"u-factors-page-{i}") for i in range(3)]
    with migrated_db.repos() as repos:
        for job_id in ids:
            repos.job_score_factors.replace(
                job_id, "keyword", [{"factor": "years", "value": 1.0}]
            )
    with migrated_db.repos() as repos:
        assert len(repos.job_score_factors.list_for_jobs(ids)) == 3
        assert repos.job_score_factors.list_for_jobs([]) == []


def test_the_same_factor_twice_for_one_method_is_refused(migrated_db: Database) -> None:
    job_id = _job(migrated_db, "u-factors-3")
    with pytest.raises(IntegrityError), migrated_db.repos() as repos:
        repos.job_score_factors.replace(
            job_id,
            "keyword",
            [{"factor": "years", "value": 0.1}, {"factor": "years", "value": 0.2}],
        )


def test_evidence_defaults_to_empty_rather_than_null(migrated_db: Database) -> None:
    job_id = _job(migrated_db, "u-factors-4")
    with migrated_db.repos() as repos:
        rows = repos.job_score_factors.replace(
            job_id, "keyword", [{"factor": "level", "value": 0.0}]
        )
        assert rows[0].evidence == ""
        assert rows[0].created_at is not None
