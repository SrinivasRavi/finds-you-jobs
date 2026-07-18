"""Adapter registry — ordered; first `detect()` claim wins.

Specific ATS adapters come first; `rss` is last because it is the generic
fallback (claims any explicit feed URL). Contribution model mirrors
career-ops: a new source = one module here + one registry line + tests.
"""

from __future__ import annotations

from types import ModuleType

from ..config import SourceEntry
from . import (
    arbeitnow,
    ashby,
    bamboohr,
    breezy,
    greenhouse,
    hackernews,
    lever,
    personio,
    recruitee,
    remoteok,
    remotive,
    rss,
    smartrecruiters,
    teamtailor,
    themuse,
    workable,
    workday,
)

ADAPTERS: list[ModuleType] = [
    greenhouse,
    lever,
    ashby,
    workable,
    smartrecruiters,
    recruitee,
    teamtailor,
    personio,
    workday,
    bamboohr,
    breezy,
    remoteok,
    remotive,
    arbeitnow,
    themuse,
    hackernews,
    rss,
]


def resolve(entry: SourceEntry) -> tuple[ModuleType, str] | None:
    """Return (adapter, source_key) for the first adapter claiming `entry`."""
    for adapter in ADAPTERS:
        claim = adapter.detect(entry)
        if claim:
            return adapter, f"{adapter.ID}:{claim}"
    return None
