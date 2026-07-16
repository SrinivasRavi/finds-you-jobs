"""Programmatic Alembic driver (architecture §5.3 boot; database-design §7).

Boot and tests both call `upgrade_to_head(url)` so the *real* migration is what
creates the schema — never a `create_all` shortcut that would drift from the
migration history.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

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
