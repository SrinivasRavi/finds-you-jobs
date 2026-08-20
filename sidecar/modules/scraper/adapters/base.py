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

from collections.abc import Container
from typing import Protocol
from urllib.parse import urlsplit

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


# ---------------------------------------------------------------------------
# Tenant-key helpers (duplication audit D-M6). Every ATS adapter derives its
# board/org/account key from the source URL, and each was re-deriving the same
# two idioms by hand. These are the one copy; adapters still own their host
# validation (allowlist, anchored regex, reserved segments) — only the parsing
# is shared.
# ---------------------------------------------------------------------------


def path_segments(url: str) -> list[str]:
    """The URL path's non-empty `/`-separated segments — leading, trailing and
    doubled slashes contribute nothing, an empty path gives `[]`."""
    return [s for s in urlsplit(url).path.split("/") if s]


def first_path_segment(url: str) -> str:
    """The board/org/account slug a career URL carries as its first path
    segment (`""` when the path has none). Validate the host first."""
    segments = path_segments(url)
    return segments[0] if segments else ""


def subdomain_tenant(url: str, suffix: str, reserved: Container[str] = ()) -> str:
    """The tenant label of a `<tenant><suffix>` hosting URL (Breezy, BambooHR),
    or `""` when the host doesn't end in `suffix`, carries a further dotted
    label, or names one of the vendor's own `reserved` subdomains."""
    host = urlsplit(url).netloc.lower() if url else ""
    if not host.endswith(suffix):
        return ""
    sub = host[: -len(suffix)]
    return "" if (not sub or "." in sub or sub in reserved) else sub
