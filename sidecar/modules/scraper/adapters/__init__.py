"""Adapter registry — ordered; first `detect()` claim wins.

Specific ATS adapters come first; `rss` is last because it is the generic
fallback (claims any explicit feed URL). Contribution model mirrors
career-ops: a new source = one module here (declaring ID, LABEL, KIND) +
one registry line + tests.
"""

from __future__ import annotations

from types import ModuleType

from ..config import SourceEntry
from . import (
    apify,
    arbeitnow,
    ashby,
    bamboohr,
    brave,
    breezy,
    greenhouse,
    hackernews,
    lever,
    linkedin_guest,
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
    linkedin_guest,
    apify,
    brave,
    hackernews,
    rss,
]

# Settings › Discover jobs rows, in ADAPTERS order: each adapter declares its
# own LABEL and KIND (base.Adapter). All sources are ON by default; the user
# opts out per family or per entry, never in.
CATALOG: dict[str, tuple[str, str]] = {a.ID: (a.LABEL, a.KIND) for a in ADAPTERS}


def resolve(entry: SourceEntry) -> tuple[ModuleType, str] | None:
    """Return (adapter, source_key) for the first adapter claiming `entry`."""
    for adapter in ADAPTERS:
        claim = adapter.detect(entry)
        if claim:
            return adapter, f"{adapter.ID}:{claim}"
    return None
