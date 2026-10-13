"""Adapter contract: the career-ops provider model, typed.

An adapter is a module exposing `ID`, `LABEL`, `KIND`, `detect(entry) -> str`
(the claim key, "" for not mine) and `fetch(entry, fetcher)`. Adapters only
normalize; `scraper.scan` filters and dedups. Each either sets
`INLINE_DESCRIPTION = True` or implements `fetch_detail` (checked by
`provides_description`). Optional `fetch_posting(url, fetcher)` serves 1 pasted
URL. Search sources (LinkedIn, Indeed, Naukri) add `search(entry, prefs,
fetcher)`, built from role aliases × locations; `scan()` prefers it."""

from __future__ import annotations

from collections.abc import Callable, Container
from typing import Protocol
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScanPrefs


class Adapter(Protocol):
    """Structural type for an adapter module (checked in tests, not at runtime).
    `LABEL` and `KIND` are its row in Settings › Discover jobs; `KIND` is ats,
    board, search or fallback. Optional: `BOARD_URLS`; a keyed adapter adds
    `CREDENTIAL` and `DEFAULT_SOURCES`, and `SUB_SOURCE_*` when each entry is
    its own toggle (F-P6)."""

    ID: str
    LABEL: str
    KIND: str

    def detect(self, entry: SourceEntry) -> str: ...

    def fetch(self, entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]: ...


class SearchAdapter(Protocol):
    """A search-shaped adapter — `search` in place of a whole-feed `fetch`."""

    ID: str

    def detect(self, entry: SourceEntry) -> str: ...

    def search(
        self, entry: SourceEntry, prefs: ScanPrefs, fetcher: Fetcher
    ) -> list[NormalizedJob]: ...


def provides_description(adapter: object) -> bool:
    """True when `adapter` satisfies the description contract (module
    docstring above): either it exposes `fetch_detail` for the scan's enrich
    phase, or it declares `INLINE_DESCRIPTION = True` — its own `fetch`/
    `search` already lands the JD in the same list request."""
    return hasattr(adapter, "fetch_detail") or bool(getattr(adapter, "INLINE_DESCRIPTION", False))


# ---------------------------------------------------------------------------
# Tenant-key helpers (D-M6): the 1 copy of the URL parsing every ATS adapter did
# by hand. Adapters still own their host validation.
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


# ---------------------------------------------------------------------------
# Shared `detect()` bodies (audit D-M17). An entry typed for another adapter is
# never claimed; the rest is the 2 shapes most adapters repeat.
# ---------------------------------------------------------------------------


def detect_url(entry: SourceEntry, adapter_id: str, tenant: Callable[[str], str]) -> str:
    """Claim an entry by its URL's tenant key (`""` when the URL has none)."""
    if (entry.type and entry.type != adapter_id) or not entry.url:
        return ""
    return tenant(entry.url)


def detect_board(entry: SourceEntry, adapter_id: str, claim: str, hosts: Container[str]) -> str:
    """Claim a whole-site job board named by `board = <id>` or by its host."""
    if entry.type and entry.type != adapter_id:
        return ""
    if entry.board == adapter_id:
        return claim
    host = urlsplit(entry.url).netloc.lower() if entry.url else ""
    return claim if host in hosts else ""
