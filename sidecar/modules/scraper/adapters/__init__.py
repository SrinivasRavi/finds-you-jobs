"""Adapter registry — ordered; first `detect()` claim wins.

Specific ATS adapters come first; `rss` is last because it is the generic
fallback (claims any explicit feed URL). Contribution model mirrors
career-ops: a new source = one module here + one registry line + tests.
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

# Display metadata for the Settings → Discovery sources toggles, keyed by
# adapter ID, in ADAPTERS order. `kind` groups the checkboxes:
# ats (company boards) | board (keyless job boards) | search (query-shaped) |
# fallback (the generic RSS claim). All sources are ON by default — the user
# opts *out* (per-family or per-entry), never in.
CATALOG: dict[str, tuple[str, str]] = {
    "greenhouse": ("Greenhouse", "ats"),
    "lever": ("Lever", "ats"),
    "ashby": ("Ashby", "ats"),
    "workable": ("Workable", "ats"),
    "smartrecruiters": ("SmartRecruiters", "ats"),
    "recruitee": ("Recruitee", "ats"),
    "teamtailor": ("Teamtailor", "ats"),
    "personio": ("Personio", "ats"),
    "workday": ("Workday", "ats"),
    "bamboohr": ("BambooHR", "ats"),
    "breezy": ("Breezy", "ats"),
    "remoteok": ("RemoteOK", "board"),
    "remotive": ("Remotive", "board"),
    "arbeitnow": ("Arbeitnow", "board"),
    "themuse": ("The Muse", "board"),
    "linkedin": ("LinkedIn (guest search)", "search"),
    "apify": ("Apify actors (your own key)", "search"),
    "brave": ("Brave Search (your own key)", "search"),
    "hackernews": ("Hacker News (Who is hiring)", "board"),
    "rss": ("RSS feeds", "fallback"),
}


def resolve(entry: SourceEntry) -> tuple[ModuleType, str] | None:
    """Return (adapter, source_key) for the first adapter claiming `entry`."""
    for adapter in ADAPTERS:
        claim = adapter.detect(entry)
        if claim:
            return adapter, f"{adapter.ID}:{claim}"
    return None
