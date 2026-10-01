"""Every stored job carries a resolved location, and legacy rows get one.

The columns exist so 3 consumers stop re-parsing 1 free-text field
(`scoring.md` section 6.6). That only holds if filling them is impossible to
forget, which is why it happens in `JobsRepo.create` rather than at the 4 call
sites that make a job, and why rows that predate the resolver converge through
the maintenance tick.
"""

from __future__ import annotations

from sidecar.app.db import Database
from sidecar.app.registry.persistence import backfill_resolved_locations


def test_creating_a_job_resolves_its_location(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="u-loc-1",
            title="Backend Engineer",
            source_adapter="lever",
            location="Bangalore, India - Remote",
        )
        assert job.location_city_id == 1277333
        assert job.location_country == "IN"
        assert job.location_admin1 == "IN.19"
        assert job.location_remote == "IN"
        assert job.location_attendance == "remote"


def test_a_job_with_no_location_stays_unresolved_rather_than_guessed(
    migrated_db: Database,
) -> None:
    """NULL is the "resolver has not seen this" state, and an empty location can
    never leave it, so the backfill must not spin on these rows forever."""
    with migrated_db.repos() as repos:
        job = repos.jobs.create(canonical_url="u-loc-2", title="T", source_adapter="lever")
        assert job.location == ""
        assert job.location_remote is None
    assert backfill_resolved_locations(migrated_db) == 0


def test_a_caller_that_already_resolved_is_left_alone(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="u-loc-3",
            title="T",
            source_adapter="lever",
            location="Bengaluru, India",
            location_remote="global",
            location_country="XX",
        )
        assert job.location_remote == "global"
        assert job.location_country == "XX"


def test_the_backfill_resolves_legacy_rows_and_converges(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        for i, where in enumerate(("Remote - USA", "Bengaluru", "Hybrid - Pune, India")):
            repos.jobs.update(
                repos.jobs.create(
                    canonical_url=f"u-loc-back-{i}",
                    title="T",
                    source_adapter="lever",
                    location=where,
                ).id,
                location_remote=None,
                location_country=None,
                location_city_id=None,
                location_admin1=None,
                location_attendance=None,
            )

    assert backfill_resolved_locations(migrated_db) == 3
    # Converges: a row it touched no longer matches its own query.
    assert backfill_resolved_locations(migrated_db) == 0

    with migrated_db.repos() as repos:
        by_url = {
            job.canonical_url: job
            for job in repos.jobs.list(feed_state=None, limit=50)
            if job.canonical_url.startswith("u-loc-back-")
        }
        assert by_url["u-loc-back-0"].location_remote == "US"
        assert by_url["u-loc-back-1"].location_city_id == 1277333
        assert by_url["u-loc-back-2"].location_attendance == "hybrid"


def test_the_backfill_is_bounded_per_call(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        for i in range(4):
            repos.jobs.update(
                repos.jobs.create(
                    canonical_url=f"u-loc-bound-{i}",
                    title="T",
                    source_adapter="lever",
                    location="Bengaluru",
                ).id,
                location_remote=None,
            )
    assert backfill_resolved_locations(migrated_db, limit=2) == 2
    assert backfill_resolved_locations(migrated_db, limit=2) == 2
    assert backfill_resolved_locations(migrated_db, limit=2) == 0
