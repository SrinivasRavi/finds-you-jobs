"""Covers: core storage — the first Alembic migration (database-design.md section 7 slice).

The real migration applies to a tmp SQLite and creates exactly the core-storage
table list; downgrade tears it back to baseline.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from sidecar.app.db.migrate import downgrade_to_base, upgrade_to_head

# Every table the migration chain creates (`docs/internal/roadmap.md` section 7.2
# #3–#8); the rest of the database-design section 7 set lands with its feature commits.
_EXPECTED = {
    "operations",
    "user_preferences",
    "master_profiles",
    "engine_settings",
    "jobs",
    "tombstones",
    "schedules",
    "applications",
    "artifacts",
    "application_events",
    "contacts",
    "company_resolutions",
    "referral_candidates",
    "outreach_logs",
    "linkedin_sessions",
    "linkedin_search_cursors",
    "apply_runs",
    "documents",
    "job_score_factors",
    "job_duplicate_dismissals",
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


def test_job_board_resolution_keys_are_purged(tmp_path: Path) -> None:
    """f8b3c6d9e4a2 (data-only): resolutions cached under a shared job-board
    domain key (the poison — e.g. `domain:naukri.com` → Coupang reused for
    every naukri job) or a bare two-level-suffix key (`domain:co.in`, minted
    by the old last-two-label parse for every `.co.in` employer) are deleted;
    per-employer domain keys — including three-label ccTLD ones — survive."""
    from alembic import command
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    # Stop just before the purge, seed a poisoned and a legit row, then run it.
    command.upgrade(make_alembic_config(url), "e7a1d4c9b2f8")

    engine = create_engine(url)
    with engine.begin() as conn:
        for i, (key, name) in enumerate(
            [
                ("domain:naukri.com", "Coupang"),
                ("domain:co.in", "Some Indian Employer"),
                ("domain:abnormal.ai", "Abnormal Security"),
                ("domain:tataelxsi.co.in", "Tata Elxsi"),
            ]
        ):
            conn.execute(
                text(
                    "INSERT INTO company_resolutions (id, resolution_key,"
                    " company_name, company_urn, company_vanity, industry,"
                    " source, resolved_at) VALUES (:id, :key, :name, '', '',"
                    " '', 'user', '2026-08-01T00:00:00')"
                ),
                {"id": f"res-{i}", "key": key, "name": name},
            )

    upgrade_to_head(url)
    with engine.connect() as conn:
        keys = {
            r[0]
            for r in conn.execute(
                text("SELECT resolution_key FROM company_resolutions")
            ).fetchall()
        }
    assert keys == {"domain:abnormal.ai", "domain:tataelxsi.co.in"}


def test_url_less_identity_preserves_existing_rows(tmp_path: Path) -> None:
    """c9e4a71b3f02 (url-less-jobs.md section 5.3): `canonical_url` goes
    nullable and identity gets its own column, with no backfill — every
    pre-migration row already has a URL and is keyed on it."""
    from alembic import command
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    command.upgrade(make_alembic_config(url), "b4d8e2a7c531")

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (id, canonical_url, title, company, location,"
                " description, source_adapter, trust_score, trust_flags,"
                " ingested_at, feed_state, feed_since, llm_reasons,"
                " llm_breakdown_md, keyword_reasons, keyword_breakdown_md,"
                " llm_score_attempts) VALUES ('j1',"
                " 'https://boards.greenhouse.io/acme/jobs/1', 'SWE', 'Acme',"
                " 'Pune', 'JD', 'greenhouse', 100, '[]',"
                " '2026-10-01T00:00:00', 'active', '2026-10-01T00:00:00',"
                " '[]', '', '[]', '', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO tombstones (id, canonical_url, reason)"
                " VALUES ('t1', 'https://boards.greenhouse.io/acme/jobs/2',"
                " 'user_delete')"
            )
        )

    upgrade_to_head(url)

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT canonical_url, content_hash, fingerprint FROM jobs")
        ).one()
        assert row[0] == "https://boards.greenhouse.io/acme/jobs/1"
        assert row[1] is None  # no backfill: this row is keyed on its URL
        assert row[2] is None
        assert (
            conn.execute(text("SELECT content_hash FROM tombstones")).scalar()
            is None
        )

        # Both populations coexist: NULLs are distinct in a unique index, so
        # any number of URL-less rows and URL rows live side by side.
        for i in (2, 3):
            conn.execute(
                text(
                    "INSERT INTO jobs (id, canonical_url, content_hash, title,"
                    " company, location, description, source_adapter,"
                    " trust_score, trust_flags, ingested_at, feed_state,"
                    " feed_since, llm_reasons, llm_breakdown_md,"
                    " keyword_reasons, keyword_breakdown_md,"
                    " llm_score_attempts) VALUES (:id, NULL, :hash, 'SWE',"
                    " 'Acme', 'Pune', 'JD', 'paste-text', 100, '[]',"
                    " '2026-10-07T00:00:00', 'active', '2026-10-07T00:00:00',"
                    " '[]', '', '[]', '', 0)"
                ),
                {"id": f"j{i}", "hash": f"hash-{i}"},
            )
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar() == 3
