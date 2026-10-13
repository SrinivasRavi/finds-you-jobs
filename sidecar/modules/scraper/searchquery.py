"""Search-query construction for the search adapters: preferences to a bounded
set of (keyword, location) queries.

Capped at `MAX_ALIASES` × `MAX_LOCATIONS` per source per scan, a fetch budget
distinct from the never-throttle rule on applications. No aliases means no
queries, surfaced as a per-source diagnostic."""

from __future__ import annotations

from dataclasses import dataclass

from .types import ScanPrefs

MAX_ALIASES = 3
MAX_LOCATIONS = 3
MAX_TERMS = 3


@dataclass(frozen=True)
class SearchQuery:
    keyword: str
    location: str  # "" = any location (the source omits the location filter)
    # True for a user-authored `search_terms` entry (constrained free-form —
    # job-finder-preferences design): the term IS the keyword, location-less,
    # and slots into each adapter's existing query template unchanged, so the
    # Brave ATS_SITES allowlist and every parser fallback keep applying.
    # Full arbitrary queries (own `site:` targets) are deliberately not built.
    user_term: bool = False


def build_queries(prefs: ScanPrefs) -> list[SearchQuery]:
    """Bounded (keyword × location) pairs from prefs, plus the user's own
    `search_terms` as location-less queries. Empty if neither is set."""
    aliases = [a.strip() for a in prefs.title_allow if a.strip()][:MAX_ALIASES]
    terms = [t.strip() for t in prefs.search_terms if t.strip()][:MAX_TERMS]
    locations = [loc.strip() for loc in prefs.location_allow if loc.strip()][:MAX_LOCATIONS]
    if not locations:
        locations = [""]  # a single location-agnostic query
    pairs = [SearchQuery(keyword=a, location=loc) for a in aliases for loc in locations]
    pairs += [SearchQuery(keyword=t, location="", user_term=True) for t in terms]
    return pairs


def select_queries(queries: list[SearchQuery], cap: int) -> list[SearchQuery]:
    """Fetch-budget selection for adapters that slice: up to `cap` computed
    alias×location pairs PLUS up to `cap` user-authored terms — an explicit
    term never crowds out the computed pairs, and vice versa."""
    computed = [q for q in queries if not q.user_term][:cap]
    terms = [q for q in queries if q.user_term][:cap]
    return computed + terms
