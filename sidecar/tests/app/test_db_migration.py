"""Covers: core storage — the first Alembic migration (database-design.md §7 slice).

The real migration applies to a tmp SQLite and creates exactly the core-storage
table list; downgrade tears it back to baseline.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from sidecar.app.db.migrate import downgrade_to_base, upgrade_to_head

# Every table the migration chain creates (`docs/internal/roadmap.md` §7.2
# #3–#8); the rest of the database-design §7 set lands with its feature commits.
_EXPECTED = {
    "operations",
    "user_preferences",
    "master_profiles",
    "profile_entities",
    "experience_skills",
    "project_skills",
    "engine_settings",
    "jobs",
    "job_scores",
    "tombstones",
    "schedules",
    "applications",
    "artifacts",
    "application_events",
    "contacts",
    "company_resolutions",
    "contact_job_assocs",
    "sequences",
    "sequence_steps",
    "outreach_logs",
    "linkedin_sessions",
    "apply_runs",
    "documents",
    "application_documents",
}


def test_migration_creates_core_tables(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_to_head(url)
    tables = set(inspect(create_engine(url)).get_table_names())
    # Exact equality (not subset) so a migration adding or dropping a table can
    # never drift past this list silently.
    assert tables == _EXPECTED | {"alembic_version"}, (
        f"missing: {_EXPECTED - tables}; unexpected: {tables - _EXPECTED - {'alembic_version'}}"
    )


def test_migration_is_reversible(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_to_head(url)
    downgrade_to_base(url)
    tables = set(inspect(create_engine(url)).get_table_names())
    # Only alembic's own bookkeeping survives a full downgrade.
    assert tables == {"alembic_version"}


def test_operations_indices_exist(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_to_head(url)
    index_names = {ix["name"] for ix in inspect(create_engine(url)).get_indexes("operations")}
    assert {"ix_operations_state_created", "ix_operations_kind_created"} <= index_names


def test_apify_identity_backfill_restamps_by_board_host(tmp_path: Path) -> None:
    """d7b2a91c4e58 (data-only): pre-existing rows the Apify adapter stored as
    `source_adapter='apify'` are re-stamped with the real board (by canonical
    host); unknown hosts keep 'apify'; non-apify rows are untouched."""
    from alembic import command
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    # Stop at the revision just before the backfill, seed pre-fix rows, then
    # run the backfill by continuing to head.
    command.upgrade(make_alembic_config(url), "a33c46cd3118")

    engine = create_engine(url)
    rows = [
        ("https://www.naukri.com/job-listings-backend-1", "apify", "naukri"),
        ("https://www.linkedin.com/jobs/view/42", "apify", "linkedin"),
        ("https://www.seek.com.au/job/7", "apify", "seek"),
        ("https://www.indeed.com/viewjob?jk=abc", "apify", "indeed"),
        ("https://weird.example.com/job/1", "apify", "apify"),  # unknown host
        ("https://boards.greenhouse.io/acme/jobs/1", "greenhouse", "greenhouse"),
    ]
    with engine.begin() as conn:
        for i, (curl, adapter, _expected) in enumerate(rows):
            conn.execute(
                text(
                    "INSERT INTO jobs (id, canonical_url, title, company, location,"
                    " description, source_adapter, trust_score, trust_flags,"
                    " feed_state, ingested_at)"
                    " VALUES (:id, :url, 'T', 'C', 'L', '', :adapter, 0, '[]',"
                    " 'active', '2026-07-18T00:00:00')"
                ),
                {"id": f"job-{i}", "url": curl, "adapter": adapter},
            )

    upgrade_to_head(url)
    with engine.connect() as conn:
        stored = {
            r[0]: r[1]
            for r in conn.execute(
                text("SELECT canonical_url, source_adapter FROM jobs")
            ).fetchall()
        }
    for curl, _adapter, expected in rows:
        assert stored[curl] == expected, curl
