"""SQLite engine wiring (architecture §6/§11, AM4; database-design §1).

WAL + `busy_timeout=5000` + `foreign_keys=ON` on every connection; single
writer through the app. The DB file lives in the platform app-data directory,
overridable with `FYJ_DATA_DIR` (tests point it at a tmp dir).

`Database.repos()` hands out a `Repos` bound to a fresh session and commits on
clean exit — the runner uses one short transaction per state change so the
global write lock is never held long (NFR-DB-02).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .repos import Repos

# Deliberately distinct from the prior repository's app-data namespace so a
# fresh install never touches the MIT-era database (roadmap §6 risk table). A
# maintainer decision before the first packaged release may still rename it;
# an explicit one-way importer is the only sanctioned migration path.
_APP_DIR_NAME = "finds-you-jobs"


def resolve_data_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    """The app-data directory. Precedence: arg > FYJ_DATA_DIR > platform default."""
    if data_dir is not None:
        return Path(data_dir)
    env = os.environ.get("FYJ_DATA_DIR")
    if env:
        return Path(env)
    return _platform_data_dir()


def _platform_data_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / _APP_DIR_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else home / "AppData" / "Local"
        return root / _APP_DIR_NAME
    # Linux / other POSIX — XDG.
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else home / ".local" / "share"
    return root / _APP_DIR_NAME


def resolve_db_url(data_dir: str | os.PathLike[str] | None = None) -> str:
    """The SQLite URL for the app DB file, creating the data dir if needed."""
    directory = resolve_data_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{directory / 'db.sqlite'}"


def _apply_sqlite_pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    """WAL + busy_timeout + FK enforcement on each pooled connection (AM4)."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:
    """Owns the engine + session factory for one SQLite file."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        # check_same_thread=False: the runner's worker threads share the engine;
        # WAL + busy_timeout (not thread affinity) provide write safety (AM4).
        self.engine: Engine = create_engine(
            url,
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False},
        )
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _apply_sqlite_pragmas)
        self._sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False)

    @classmethod
    def from_env(cls, data_dir: str | os.PathLike[str] | None = None) -> Database:
        return cls(resolve_db_url(data_dir))

    @property
    def data_dir(self) -> Path:
        """The directory holding this DB file — where co-located blobs (uploaded
        documents) live. Derived from the URL, not the env, so it always matches
        the DB even when the DB was built with an explicit path (tests)."""
        prefix = "sqlite:///"
        if self.url.startswith(prefix):
            return Path(self.url[len(prefix):]).parent
        return resolve_data_dir()

    def session(self) -> Session:
        return self._sessionmaker()

    @contextmanager
    def repos(self) -> Iterator[Repos]:
        """A `Repos` on a fresh session; commit on clean exit, rollback on error."""
        session = self._sessionmaker()
        try:
            yield Repos(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
