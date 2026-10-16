"""Covers: core storage — the first Alembic migration (database-design.md section 7 slice).

The real migration applies to a tmp SQLite and creates exactly the core-storage
table list; downgrade tears it back to baseline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
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
    "job_duplicates",
    "chats",
    "chat_messages",
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
        # The URL tombstone survives; the text column it once had is gone (e3a8c51f7d20).
        assert conn.execute(text("SELECT canonical_url FROM tombstones")).scalar()

        # Both populations coexist: NULLs are distinct in a unique index, so
        # any number of URL-less rows and URL rows live side by side.
        for i in (2, 3):
            conn.execute(
                text(
                    "INSERT INTO jobs (id, canonical_url, content_hash, title,"
                    " company, location, description, source_adapter,"
                    " trust_score, trust_flags, ingested_at, feed_state,"
                    " feed_since, llm_score_attempts) VALUES (:id, NULL, :hash,"
                    " 'SWE', 'Acme', 'Pune', 'JD', 'paste-text', 100, '[]',"
                    " '2026-10-07T00:00:00', 'active', '2026-10-07T00:00:00', 0)"
                ),
                {"id": f"j{i}", "hash": f"hash-{i}"},
            )
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar() == 3


def test_duplicate_flags_and_dismissals_become_1_row_per_pair(tmp_path: Path) -> None:
    """e3a8c51f7d20 copies the JSON flags and the dismissals into job_duplicates,
    strips the flags, and marks hand-added rows `manual`. A legacy flag today's
    compound gate rejects is dropped rather than carried over."""
    import json

    from alembic import command
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'dup.db'}"
    command.upgrade(make_alembic_config(url), "d1c5f83a9b27")
    engine = create_engine(url)
    cols = (
        "id, canonical_url, title, company, location, description, source_adapter, "
        "trust_score, trust_flags, ingested_at, feed_state, feed_since"
    )
    rows = [
        ("a", "https://ex.co/a", "greenhouse", []),
        ("b", None, "paste-text", ["probable-duplicate:a", "duplicate-batch:1", "stale-posting"]),
        ("c", "https://ex.co/c", "lever", ["probable-cross-listing:https://ex.co/a"]),
        ("d", "https://ex.co/d", "lever", ["probable-cross-listing:https://ex.co/a"]),
    ]
    jd = (
        "Senior Software Engineer at Acme. You will own distributed Python services, "
        "design PostgreSQL schemas, run workloads on AWS and mentor other engineers."
    )
    with engine.begin() as conn:
        for job_id, link, source, flags in rows:
            conn.execute(
                text(
                    f"INSERT INTO jobs ({cols}) VALUES (:id, :url, 'SWE', 'Acme', '', :jd, "  # noqa: S608 — fixed column list
                    ":src, 100, :flags, '2026-10-01', 'active', '2026-10-01')"
                ),
                {"id": job_id, "url": link, "src": source, "flags": json.dumps(flags), "jd": jd},
            )
        # Written by the old bare threshold: same text, a different level.
        conn.execute(text("UPDATE jobs SET title = 'Principal SWE' WHERE id = 'd'"))
        conn.execute(
            text(
                "INSERT INTO job_duplicate_dismissals (id, low_job_id, high_job_id, created_at) "
                "VALUES ('d1', 'a', 'c', '2026-10-02')"
            )
        )
    upgrade_to_head(url)
    with engine.begin() as conn:
        pairs = {
            (r[0], r[1], r[2] is not None)
            for r in conn.execute(
                text("SELECT job_id, other_job_id, dismissed_at FROM job_duplicates")
            )
        }
        assert pairs == {("b", "a", False), ("c", "a", True)}
        flag_rows = conn.execute(text("SELECT id, trust_flags FROM jobs"))
        flags = {str(r[0]): str(r[1]) for r in flag_rows}
        assert json.loads(flags["b"]) == ["stale-posting"]
        assert json.loads(flags["c"]) == []
        origins = {str(r[0]): str(r[1]) for r in conn.execute(text("SELECT id, origin FROM jobs"))}
        assert origins == {"a": "scan", "b": "manual", "c": "scan", "d": "scan"}


def test_existing_operations_read_as_started_by_the_app(tmp_path: Path) -> None:
    """a1c7e5d2b9f4 (audit A1): every operation that predates `requested_by` reads
    `app`, so nothing old jumps the queue; the downgrade drops the column."""
    from alembic import command
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'ops.db'}"
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "f7c2d4e9a1b6")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO operations (id, kind, state, input_snapshot, created_at) "
            "VALUES ('o1', 'score', 'queued', '{}', '2026-10-01 00:00:00')"
        ))
    command.upgrade(cfg, "a1c7e5d2b9f4")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT requested_by FROM operations")).scalar() == "app"
    command.downgrade(cfg, "f7c2d4e9a1b6")
    cols = {c["name"] for c in inspect(engine).get_columns("operations")}
    assert "requested_by" not in cols


def test_one_score_per_job_keeps_the_shown_score_and_its_factors(tmp_path: Path) -> None:
    """b3e9c4a1d7f2 (audit A3): the LLM score wins over the keyword floor, a keyword-only
    job keeps its keyword score, only the shown method's factors move onto the row,
    and the downgrade rebuilds both column sets."""
    import json

    from alembic import command
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'score.db'}"
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "a1c7e5d2b9f4")
    engine = create_engine(url)
    base = (
        "INSERT INTO jobs (id, canonical_url, title, company, location, description,"
        " source_adapter, trust_score, trust_flags, ingested_at, feed_state, feed_since,"
        " llm_score, llm_score_method, llm_reasons, llm_breakdown_md, keyword_score,"
        " keyword_reasons, keyword_breakdown_md, llm_score_attempts) VALUES (:id, :id,"
        " 'SWE', 'Acme', 'Pune', 'JD', 'lever', 100, '[]', '2026-10-01T00:00:00',"
        " 'active', '2026-10-01T00:00:00', :llm, :m, '[\"ai\"]', 'ai md', :kw,"
        " '[\"kw\"]', 'kw md', 0)"
    )
    factor = (
        "INSERT INTO job_score_factors (id, job_id, method, factor, value, kind, evidence,"
        " created_at) VALUES (:id, :job, :m, :f, :v, 'graded', '', '2026-10-02 00:00:00')"
    )
    with engine.begin() as conn:
        conn.execute(text(base), {"id": "ai", "llm": 81, "m": "jev", "kw": 40})
        conn.execute(text(base), {"id": "kw", "llm": None, "m": None, "kw": 33})
        conn.execute(text(base), {"id": "none", "llm": None, "m": None, "kw": None})
        for i, (job, m, f, v) in enumerate([
            ("ai", "jev", "core_stack", 0.9), ("ai", "keyword", "core_stack", 0.2),
            ("kw", "keyword", "years", 0.5),
        ]):
            conn.execute(text(factor), {"id": f"f{i}", "job": job, "m": m, "f": f, "v": v})
    command.upgrade(cfg, "b3e9c4a1d7f2")
    with engine.connect() as conn:
        rows = {
            r[0]: r[1:]
            for r in conn.execute(text(
                "SELECT id, score, score_method, score_reasons, score_factors, scored_at"
                " FROM jobs"
            ))
        }
    assert rows["ai"][:2] == (81, "jev")
    assert json.loads(rows["ai"][2]) == ["ai"]
    assert [f["value"] for f in json.loads(rows["ai"][3])] == [0.9]
    assert rows["ai"][4] is not None
    assert rows["kw"][:2] == (33, "keyword")
    assert rows["none"][:2] == (None, None)
    assert "job_score_factors" not in inspect(engine).get_table_names()
    command.downgrade(cfg, "a1c7e5d2b9f4")
    with engine.connect() as conn:
        back = conn.execute(text(
            "SELECT llm_score, llm_score_method, keyword_score FROM jobs WHERE id = 'ai'"
        )).one()
        assert tuple(back) == (81, "jev", None)
        assert conn.execute(text("SELECT count(*) FROM job_score_factors")).scalar() == 2


def test_duplicate_migration_hashes_only_the_jobs_a_flag_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """e3a8c51f7d20 (audit A12): 1,000 unflagged jobs cost the boot nothing; only the 3
    jobs named in a pair flag are fingerprinted. Alembic re-imports a revision on each
    run, so the module is loaded here and its own `upgrade()` run, to count the calls."""
    import importlib.util
    import json

    from alembic import command
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'hash.db'}"
    command.upgrade(make_alembic_config(url), "d1c5f83a9b27")
    engine = create_engine(url)
    jd = (
        "Senior Software Engineer at Acme. You will own distributed Python services, "
        "design PostgreSQL schemas, run workloads on AWS and mentor other engineers."
    )
    insert = text(
        "INSERT INTO jobs (id, canonical_url, title, company, location, description,"
        " source_adapter, trust_score, trust_flags, ingested_at, feed_state, feed_since)"
        " VALUES (:id, :url, 'SWE', 'Acme', '', :jd, 'lever', 100, :flags, '2026-10-01',"
        " 'active', '2026-10-01')"
    )
    flagged = {
        "a": [],
        "b": ["probable-duplicate:a", "duplicate-batch:1"],
        "c": ["probable-cross-listing:https://ex.co/a", "probable-duplicate:gone"],
    }
    with engine.begin() as conn:
        for job_id, flags in flagged.items():
            conn.execute(
                insert,
                {"id": job_id, "url": f"https://ex.co/{job_id}", "jd": jd,
                 "flags": json.dumps(flags)},
            )
        for i in range(1000):
            conn.execute(
                insert,
                {"id": f"plain-{i}", "url": f"https://ex.co/p{i}", "jd": f"{jd} {i}",
                 "flags": "[]"},
            )

    path = (
        Path(__file__).parents[2]
        / "app/db/alembic/versions/20261012_e3a8c51f7d20_job_duplicates_and_origin.py"
    )
    spec = importlib.util.spec_from_file_location("e3a8c51f7d20", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hashed: list[str] = []
    real = module._simhash64

    def counting(description: str) -> int:
        hashed.append(description)
        return real(description)

    monkeypatch.setattr(module, "_simhash64", counting)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.upgrade()

    assert len(hashed) == 3
    with engine.connect() as conn:
        pairs = set(conn.execute(text("SELECT job_id, other_job_id FROM job_duplicates")))
    assert pairs == {("b", "a"), ("c", "a")}


def test_the_5_location_columns_go_and_come_back_empty(tmp_path: Path) -> None:
    """c5f1a8d3e6b2 (B6, place-picker.md 10.5 item 2): the columns nothing read are
    dropped with the job kept; the downgrade restores them as NULL, which the old
    daily pass reads as "never resolved" and refills."""
    from alembic import command
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'loc.db'}"
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "b3e9c4a1d7f2")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO jobs (id, canonical_url, title, company, location, description,"
            " source_adapter, trust_score, trust_flags, ingested_at, feed_state, feed_since,"
            " location_country, location_admin1, location_city_id, location_remote,"
            " location_attendance) VALUES ('j', 'https://ex.co/j', 'SWE', 'Acme',"
            " 'Bengaluru (Hybrid)', 'JD', 'lever', 100, '[]', '2026-10-01', 'active',"
            " '2026-10-01', 'IN', 'IN.19', 1277333, '', 'hybrid')"
        ))
    names = ("location_country", "location_admin1", "location_city_id", "location_remote",
             "location_attendance")

    command.upgrade(cfg, "c5f1a8d3e6b2")
    cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert not cols & set(names)
    with engine.connect() as conn:
        location = conn.execute(text("SELECT location FROM jobs WHERE id = 'j'")).scalar()
    assert location == "Bengaluru (Hybrid)"

    command.downgrade(cfg, "b3e9c4a1d7f2")
    with engine.connect() as conn:
        back = conn.execute(text(f"SELECT {', '.join(names)} FROM jobs WHERE id = 'j'")).one()  # noqa: S608 — fixed names
    assert tuple(back) == (None,) * 5


def test_smartrecruiters_links_move_to_the_shape_the_site_serves(tmp_path: Path) -> None:
    """Audit 2026-10-14 section 8.4: every stored `/postings/<id>` link opened a 404.
    Jobs and tombstones move to `/<company>/<id>`; a row whose new link is taken
    keeps its old one rather than break the unique key."""
    from alembic import command
    from sqlalchemy import text

    from sidecar.app.db.migrate import make_alembic_config

    url = f"sqlite:///{tmp_path / 'sr.db'}"
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "c5f1a8d3e6b2")
    engine = create_engine(url)
    old = "https://jobs.smartrecruiters.com/Acme/postings/{}"
    new = "https://jobs.smartrecruiters.com/Acme/{}"
    insert = (
        "INSERT INTO jobs (id, canonical_url, title, company, location, description,"
        " source_adapter, trust_score, trust_flags, ingested_at, feed_state, feed_since)"
        " VALUES (:id, :url, 'SWE', 'Acme', '', 'JD', 'smartrecruiters', 100, '[]',"
        " '2026-10-01', 'active', '2026-10-01')"
    )
    with engine.begin() as conn:
        conn.execute(text(insert), {"id": "a", "url": old.format(1)})
        conn.execute(text(insert), {"id": "b", "url": old.format(2)})
        conn.execute(text(insert), {"id": "c", "url": new.format(2)})  # pasted already
        conn.execute(text(insert), {"id": "d", "url": "https://jobs.lever.co/x/postings/9"})
        conn.execute(text("INSERT INTO tombstones (id, canonical_url, reason)"
                          " VALUES ('t', :u, 'manual')"), {"u": old.format(3)})

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        urls = {r[0]: r[1] for r in conn.execute(text("SELECT id, canonical_url FROM jobs"))}
        tomb = conn.execute(text("SELECT canonical_url FROM tombstones")).scalar()
    assert urls == {
        "a": new.format(1),
        "b": old.format(2),  # its new link belongs to c
        "c": new.format(2),
        "d": "https://jobs.lever.co/x/postings/9",
    }
    assert tomb == new.format(3)
    # b and c are 1 posting twice: the duplicate notice asks which to keep.
    with engine.connect() as conn:
        pairs = conn.execute(text("SELECT job_id, other_job_id FROM job_duplicates")).all()
    assert len(pairs) == 1 and set(pairs[0]) == {"b", "c"}

    command.downgrade(cfg, "c5f1a8d3e6b2")
    with engine.connect() as conn:
        back = conn.execute(text("SELECT canonical_url FROM jobs WHERE id = 'a'")).scalar()
    assert back == old.format(1)
