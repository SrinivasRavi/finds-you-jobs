# jobapplier.upstream.constants — AGPL-3.0 subtree (see LICENSE).
# SPDX-License-Identifier: AGPL-3.0-only
#
# Adapted from Skyvern @ 28db09cb (skyvern/constants.py). Only the single
# constant the carried observation core references is taken: SKYVERN_ID_ATTR.
# This module additionally hosts the stdlib-logging adapter that replaces the
# upstream `structlog` dependency (trim rule: structlog -> logging).
#
# Trims:
#   - structlog -> stdlib `logging`. `get_logger()` returns a tiny adapter that
#     accepts structlog-style keyword event fields so the carried call sites
#     (e.g. `LOG.info("msg", frame_index=3)`) stay byte-identical; the fields
#     are appended to the formatted message. Logger name: `fyj.jobapplier.upstream`.
"""Constants + logging adapter for the Skyvern-derived observation core."""

from __future__ import annotations

import logging
from typing import Any

# skyvern/constants.py:5 — the attribute domUtils.js stamps on each element.
SKYVERN_ID_ATTR: str = "unique_id"

_LOGGER_NAME = "fyj.jobapplier.upstream"


class _StructlogShim:
    """Minimal structlog-style facade over a stdlib logger.

    Upstream used `structlog`, whose bound loggers accept arbitrary keyword
    event fields. To keep the carried call sites byte-identical without adding
    the structlog dependency, this adapter accepts those keyword fields and
    appends ``key=value`` pairs to the message. ``exc_info`` is passed through
    to stdlib. This adapter is finds-you-jobs-owned glue, not carried code.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        exc_info = fields.pop("exc_info", False)
        if fields:
            rendered = " ".join(f"{key}={value!r}" for key, value in fields.items())
            event = f"{event} {rendered}"
        self._logger.log(level, event, exc_info=exc_info)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)

    def exception(self, event: str, **fields: Any) -> None:
        fields.setdefault("exc_info", True)
        self._emit(logging.ERROR, event, **fields)


def get_logger() -> _StructlogShim:
    """structlog.get_logger()-shaped factory returning the stdlib adapter."""
    return _StructlogShim(logging.getLogger(_LOGGER_NAME))
