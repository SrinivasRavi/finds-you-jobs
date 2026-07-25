"""Flight-recorder log — the internal debug layer (architecture §10, layer 3).

stdlib `logging` → a rotating file handler. This is the net for failures that
happen before/outside the Logfire span pipeline and the DB (boot, handshake,
runner internals, migration errors) — never user-facing.

Dev location: `<repo>/logs/sidecar.log`. The app-data path swaps in later by
passing `log_dir` (or setting `FYJ_LOG_DIR`); nothing else changes.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "fyj.sidecar"

# Repo root = sidecar/app/logging_setup.py -> parents[2]. Swapped for the
# platform app-data dir in the packaged build (see FYJ_LOG_DIR below).
_REPO_ROOT = Path(__file__).resolve().parents[2]

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3


def resolve_log_dir(log_dir: str | os.PathLike[str] | None = None) -> Path:
    """Where the flight recorder writes. Precedence: arg > FYJ_LOG_DIR > repo/logs."""
    if log_dir is not None:
        return Path(log_dir)
    env = os.environ.get("FYJ_LOG_DIR")
    if env:
        return Path(env)
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

    # Idempotence: don't add a second handler for the same file.
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(
            log_path.resolve()
        ):
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
    # Defense-in-depth redaction net: scrub any secret-shaped substring that a
    # future code path slips into a log line or traceback before it lands in the
    # flight recorder. Source discipline stays the primary control; this is the
    # safety net (see observability/redaction.py). Attached at the handler so it
    # also covers records propagated from child loggers (fyj.sidecar.*).
    # Local import: the observability package imports get_logger from this module,
    # so a top-level import here would be circular at boot.
    from .observability.redaction import attach_redaction

    attach_redaction(handler)
    logger.addHandler(handler)
    return log_path


def get_logger() -> logging.Logger:
    """The sidecar's flight-recorder logger."""
    return logging.getLogger(LOGGER_NAME)
