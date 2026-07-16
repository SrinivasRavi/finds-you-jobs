"""Covers: core storage — the first Alembic migration (database-design.md §7 slice).

The real migration applies to a tmp SQLite and creates exactly the core-storage
table list; downgrade tears it back to baseline.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from sidecar.app.db.migrate import downgrade_to_base, upgrade_to_head

# The core-storage table list (`docs/internal/roadmap.md` §7.2 #3); the rest of
# the database-design §7 set lands with its feature commits.
_EXPECTED = {
    "operations",
    "user_preferences",
}


def test_migration_creates_core_tables(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_to_head(url)
    tables = set(inspect(create_engine(url)).get_table_names())
    assert _EXPECTED <= tables, f"missing: {_EXPECTED - tables}"
    assert "alembic_version" in tables


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
