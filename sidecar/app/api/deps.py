"""Shared `app.state` accessors for the routers (D-A9).

Every router needs the same three handles off `request.app.state`, with the same
"not initialized yet → 503" answer (a request that lands during boot must get an
honest 503, never an AttributeError 500). They were pasted per router and had
already drifted — `discovery.py`'s `data_dir` returned the raw state value while
`engines.py`'s returned a `Path`. One definition each, typed once, here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from ..db import Database
from ..registry import EngineRegistry


def db(request: Request) -> Database:
    database = getattr(request.app.state, "db", None)
    if database is None:
        raise HTTPException(status_code=503, detail="storage not initialized")
    return database


def data_dir(request: Request) -> Path:
    resolved = getattr(request.app.state, "data_dir", None)
    if resolved is None:
        raise HTTPException(status_code=503, detail="data dir not initialized")
    return Path(resolved)


def engines(request: Request) -> EngineRegistry | None:
    """The engine registry, or None when the app was built without one (tests /
    a boot that failed to configure providers) — callers degrade, never 503."""
    return getattr(request.app.state, "engines", None)
