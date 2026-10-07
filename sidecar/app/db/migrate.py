"""Programmatic Alembic driver (architecture section 5.3 boot; database-design section 7).

Boot and tests both call `upgrade_to_head(url)` so the *real* migration is what
creates the schema — never a `create_all` shortcut that would drift from the
migration history.
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_DB_DIR = Path(__file__).resolve().parent  # sidecar/app/db
_ALEMBIC_INI = _DB_DIR / "alembic.ini"
_SCRIPT_LOCATION = _DB_DIR / "alembic"


def make_alembic_config(db_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def upgrade_to_head(db_url: str) -> None:
    command.upgrade(make_alembic_config(db_url), "head")


def downgrade_to_base(db_url: str) -> None:
    command.downgrade(make_alembic_config(db_url), "base")


def _stored_revision(db_url: str) -> str | None:
    """The revision the database says it is at, or None if unreadable."""
    try:
        engine = sa.create_engine(db_url)
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT version_num FROM alembic_version")).first()
        engine.dispose()
    except Exception:  # noqa: BLE001 — this runs while reporting another failure
        return None
    return str(row[0]) if row else None


def _known_head(db_url: str) -> str | None:
    try:
        return ScriptDirectory.from_config(make_alembic_config(db_url)).get_current_head()
    except Exception:  # noqa: BLE001 — same
        return None


def report_migration_failure(db_url: str, exc: BaseException) -> None:
    """Print why boot died, to stderr, where the dev terminal and the shell's
    captured sidecar output both show it. Alembic's fileConfig has disabled the
    loggers by this point, so a bare raise reaches nobody."""
    stored = _stored_revision(db_url)
    head = _known_head(db_url)
    lines = [
        "sidecar: the schema migration failed, so the backend can't start.",
        f"  database:        {db_url}",
        f"  stored revision: {stored or 'unreadable'}",
        f"  this build ends at: {head or 'unknown'}",
        f"  reason:          {exc}",
    ]
    # An unresolvable stored revision means the database was last opened by a
    # build whose migration history this one doesn't have, which on a dev
    # machine is almost always a branch switch.
    if stored and head and stored != head and "locate revision" in str(exc):
        lines += [
            "",
            "That revision isn't in this build's history, so the database was last",
            "opened by another branch or a newer build. Either check out the branch",
            "that carries it, or point this one at its own profile with",
            "FYJ_DATA_DIR=/path/to/new-profile.",
        ]
    print("\n".join(lines), file=sys.stderr, flush=True)
