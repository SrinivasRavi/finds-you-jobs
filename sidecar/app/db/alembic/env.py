"""Alembic environment for the finds-you-jobs app schema.

The DB URL comes from the Alembic config's `sqlalchemy.url` when set
programmatically (migrate.py / tests), otherwise from `FYJ_DATA_DIR` via the
app's own resolver — so a plain `alembic upgrade head` targets the real
app-data DB. `target_metadata` is the app `Base.metadata`, enabling
autogenerate.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import models for their side effect: registering every table on Base.metadata.
from sidecar.app.db import models  # noqa: F401
from sidecar.app.db.base import Base
from sidecar.app.db.database import resolve_db_url

config = context.config

if config.config_file_name is not None:
    # Keep fileConfig's default (`disable_existing_loggers=True`). It DOES flip
    # `.disabled = True` on the app's `fyj.sidecar` flight-recorder logger — the
    # "only boot lines land" bug — but flipping it to False perturbs global logging
    # state that span export depends on (an ordering-dependent test failure).
    # Instead the boot path re-arms the recorder AFTER the migration (main.py
    # calls setup_flight_recorder() again, clearing `.disabled`).
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _db_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return resolve_db_url()


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-safe ALTERs for future migrations.
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
