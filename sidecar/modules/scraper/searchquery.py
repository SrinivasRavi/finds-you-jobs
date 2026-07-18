"""Search-query construction shared by the search adapters (LinkedIn/Indeed/
Naukri) — turn the user's preferences into a bounded set of (keyword, location)
queries.

A search source can't enumerate a whole site, so it queries per role alias ×
location. Left unbounded that is a combinatorial fetch explosion, so the count
is capped: at most `MAX_ALIASES` × `MAX_LOCATIONS` pairs per source per scan.
This is a *fetch* budget (per-IP courtesy + latency), distinct from the
never-throttle rule on *application* volume — see the vision ethos.

An empty alias list yields no queries: a keyword-less search would pull an
entire job board, which is neither useful nor kind to the endpoint. The caller
surfaces that as a clear per-source diagnostic rather than fetching blindly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import ScanPrefs

MAX_ALIASES = 3
MAX_LOCATIONS = 3


@dataclass(frozen=True)
class SearchQuery:
    keyword: str
    location: str  # "" = any location (the source omits the location filter)


def build_queries(prefs: ScanPrefs) -> list[SearchQuery]:
    """Bounded (keyword × location) pairs from prefs. Empty if no role alias."""
    aliases = [a.strip() for a in prefs.title_allow if a.strip()][:MAX_ALIASES]
    if not aliases:
        return []
    locations = [loc.strip() for loc in prefs.location_allow if loc.strip()][:MAX_LOCATIONS]
    if not locations:
        locations = [""]  # a single location-agnostic query
    return [SearchQuery(keyword=a, location=loc) for a in aliases for loc in locations]
