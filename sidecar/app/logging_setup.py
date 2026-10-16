"""Flight-recorder log — the internal debug layer (architecture section 10, layer 3).

stdlib `logging` → a rotating file handler. This is the net for failures that
happen before/outside the Logfire span pipeline and the DB (boot, handshake,
runner internals, migration errors) — never user-facing.

Dev location: `<repo>/logs/sidecar.log`. Packaged (PyInstaller) location: the
platform app-data dir, beside the database. `log_dir` / `FYJ_LOG_DIR` override
both.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "fyj.sidecar"
# The vendored packages log under their own roots: the LinkedIn worker as
# `voyager_py.*`, the Applier as `fyj.jobapplier.*`. At INFO, because the mouse
# and scroll loggers write a DEBUG line per step.
_PACKAGE_LOGGERS = ("voyager_py", "fyj.jobapplier")

# Repo root = sidecar/app/logging_setup.py -> parents[2]. Swapped for the
# platform app-data dir in the packaged build (see FYJ_LOG_DIR below).
_REPO_ROOT = Path(__file__).resolve().parents[2]

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3


def resolve_log_dir(log_dir: str | os.PathLike[str] | None = None) -> Path:
    """Where the flight recorder writes.

    Precedence: arg > FYJ_LOG_DIR > app-data dir (packaged) > repo/logs (dev).
    """
    if log_dir is not None:
        return Path(log_dir)
    env = os.environ.get("FYJ_LOG_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        # PyInstaller build: _REPO_ROOT is inside the signed bundle, which must never be
        # written (macOS blocks it, and a write breaks notarization), so logs go to the
        # app-data dir. Local import: db.database must load after __main__ wires boot.
        from .db.database import resolve_data_dir

        return resolve_data_dir() / "logs"
    return _REPO_ROOT / "logs"


def setup_flight_recorder(
    log_dir: str | os.PathLike[str] | None = None,
    *,
    level: int = logging.DEBUG,
) -> Path:
    """Attach the rotating file handler to the sidecar logger. Returns the log path.

    Idempotent: repeated calls don't stack handlers.
    """
    directory = resolve_log_dir(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "sidecar.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    # Defensive re-arm: a `logging.config.fileConfig`/`dictConfig` elsewhere in the
    # boot path (Alembic's migration env is the known offender) can flip this logger
    # to `.disabled = True` and silently drop everything after it. Clearing it on
    # every call — including the idempotent re-entry below — keeps the flight
    # recorder alive across migrations.
    logger.disabled = False

    # Idempotence: no second handler for the same file. Both sides resolve, because
    # `baseFilename` keeps symlinks: under /tmp on macOS the guard never matched and
    # every boot-path call stacked another handler.
    resolved = log_path.resolve()
    for handler in logger.handlers:
        if (
            isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename).resolve() == resolved
        ):
            _attach_packages(handler)
            return log_path

    handler = RotatingFileHandler(
        log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s [pid=%(process)d] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    # Redaction net: scrub secret-shaped substrings before a line reaches the flight
    # recorder (observability/redaction.py). Source discipline is the primary control.
    # At the handler, so child loggers are covered; local import to avoid a cycle.
    from .observability.redaction import attach_redaction

    attach_redaction(handler)
    logger.addHandler(handler)
    _attach_packages(handler)
    return log_path


def _attach_packages(handler: logging.Handler) -> None:
    for name in _PACKAGE_LOGGERS:
        package = logging.getLogger(name)
        package.setLevel(logging.INFO)
        package.propagate = False
        package.disabled = False
        if handler not in package.handlers:
            package.addHandler(handler)
        # A config call with `disable_existing_loggers` (Alembic's) disables each
        # child logger already created, not just the root of the tree.
        for child_name, child in logging.root.manager.loggerDict.items():
            if child_name.startswith(name + ".") and isinstance(child, logging.Logger):
                child.disabled = False


def get_logger() -> logging.Logger:
    """The sidecar's flight-recorder logger."""
    return logging.getLogger(LOGGER_NAME)
