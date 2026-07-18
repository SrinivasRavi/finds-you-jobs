"""Adapter contract — the career-ops provider model, typed.

An adapter is a module (not a class) exposing:

    ID: str                                   # source_adapter value on every job
    def detect(entry: SourceEntry) -> str     # claim key ("" = not mine)
    def fetch(entry, fetcher) -> list[NormalizedJob]

`detect` returns the tenant/host part of the source key (e.g. the Greenhouse
board slug) so diagnostics read `greenhouse:gleanwork`. Adapters normalize and
nothing else: no filtering, no dedup, no quality scoring — the shared pipeline
(`scraper.scan`) does that for every source. One list request per source, using
the API's content params where they exist so the JD `description` lands in that
same request (Greenhouse `content=true`, Workable `details=true`, Lever/Ashby
native `descriptionPlain`); per-job detail fetch only as a documented fallback
(maintainer decision 2026-07-07, JD-description gap — none needed as-built).

**Two source shapes (discovery-expansion 2026-07-17).**

- *Enumerate* sources (every ATS + keyless board) list a company's or board's
  whole feed with `fetch(entry, fetcher)`. The shared pipeline's title/location
  filters then narrow the result.
- *Search* sources (LinkedIn/Indeed/Naukri) can't enumerate the whole site —
  the query IS the filter, applied server-side. They additionally expose
  `search(entry, prefs, fetcher) -> list[NormalizedJob]`, building queries from
  the user's role aliases (`prefs.title_allow`) × locations
  (`prefs.location_allow`). `scan()` calls `search` when present, else `fetch`.
  The same downstream filter chain still runs — the query reduces volume; the
  local filters refine — so behavior stays consistent across shapes.

Registry + auto-detection live in `adapters/__init__.py`.
"""

from __future__ import annotations

from typing import Protocol

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScanPrefs


class Adapter(Protocol):
    """Structural type for an adapter module (checked in tests, not at runtime)."""

    ID: str

    def detect(self, entry: SourceEntry) -> str: ...

    def fetch(self, entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]: ...


class SearchAdapter(Protocol):
    """A search-shaped adapter — `search` in place of a whole-feed `fetch`."""

    ID: str

    def detect(self, entry: SourceEntry) -> str: ...

    def search(
        self, entry: SourceEntry, prefs: ScanPrefs, fetcher: Fetcher
    ) -> list[NormalizedJob]: ...
