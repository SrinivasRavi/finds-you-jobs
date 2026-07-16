"""Storage layer (architecture §5, database-design.md).

SQLAlchemy 2.0 models + Alembic migrations + the `Repos` interface. Business
code (routes, runner, scheduler) goes through `Repos`, never a raw session.
The one-way rule (architecture §5.2) still holds: this lives in `app/`, imports
`modules/` freely, and `modules/` never imports back.
"""

from __future__ import annotations

from .database import Database, resolve_data_dir, resolve_db_url
from .ids import uuid7
from .repos import Repos

__all__ = ["Database", "Repos", "resolve_data_dir", "resolve_db_url", "uuid7"]
