"""Add-by-URL probe — resolve a single pasted job URL to a `NormalizedJob`.

The user escape hatch (US-JB-07): the user pastes one posting URL and we make a
best-effort extraction of title / company / location / description. This is
*not* scan()'s one-request-per-source path — it's an explicit single-URL user
action, so a per-URL fetch is exactly right here:

- If a known ATS / board adapter claims the URL shape, fetch that board (one
  request) and return the row whose canonical URL matches the pasted one —
  Greenhouse / Lever / Ashby job URLs come back fully structured, description
  included (the content-in-list params from the JD-gap work).
- Otherwise fetch the page directly and pull a best-effort title + text body from
  the HTML (generic careers pages, Workable job links, anything else).

20 s fetch timeout, no auto-retry (US-JB-07, user-stories §17b): the caller passes
`timeout_s`; a fetch failure raises `ScraperError` verbatim and the user can then
fill the fields in by hand. The result is *not* persisted — the app owns that.
"""

from __future__ import annotations

import re

from . import adapters
from .canonical import canonicalize_url
from .config import SourceEntry
from .htmltext import strip_html
from .http import Fetcher
from .types import NormalizedJob, ScraperError

_PASTE_ADAPTER = "paste-url"
# Cap the generic-page body so a nav/footer-heavy page can't balloon the JD.
_MAX_BODY_CHARS = 20_000


def probe_url(
    url: str,
    fetcher_factory: type[Fetcher] = Fetcher,
    timeout_s: int = 20,
) -> NormalizedJob:
    """Best-effort resolve one pasted job URL to a `NormalizedJob` (not persisted)."""
    canonical = canonicalize_url(url)
    if not canonical:
        raise ScraperError("probe", f"not a usable http(s) URL: {url!r}")

    entry = SourceEntry(url=url)
    resolved = adapters.resolve(entry)
    fetcher = fetcher_factory(timeout_s=timeout_s)

    if resolved is not None:
        adapter, _key = resolved
        matched = _match_from_board(adapter, entry, fetcher, canonical)
        if matched is not None:
            return matched
        # The board claimed the URL but it wasn't a specific job we could match
        # (e.g. a board root, or a shape the list doesn't expose) — fall through
        # to generic extraction so the user still gets a pre-filled draft.

    return _generic_probe(url, canonical, fetcher)


def _match_from_board(
    adapter: object, entry: SourceEntry, fetcher: Fetcher, canonical: str
) -> NormalizedJob | None:
    """Fetch the claiming board and return the row matching the pasted URL."""
    jobs = adapter.fetch(entry, fetcher)  # type: ignore[attr-defined]
    for job in jobs:
        if canonicalize_url(job.canonical_url) == canonical:
            job.canonical_url = canonical
            return job
    return None


def _generic_probe(url: str, canonical: str, fetcher: Fetcher) -> NormalizedJob:
    """Pull a best-effort title + body text from an arbitrary posting page."""
    page = fetcher.get_text(url)
    return NormalizedJob(
        title=_extract_title(page),
        canonical_url=canonical,
        description=_extract_body(page),
        source_adapter=_PASTE_ADAPTER,
    )


def _extract_title(page: str) -> str:
    """Prefer the first <h1> (usually the role), else the <title> tag."""
    h1 = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page)
    if h1:
        text = strip_html(h1.group(1))
        if text:
            return text
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    return strip_html(title.group(1)) if title else ""


def _extract_body(page: str) -> str:
    """Text of <main>/<article> if present (tighter than the whole page), else
    the full document — stripped to plain text and capped."""
    for tag in ("main", "article"):
        m = re.search(rf"(?is)<{tag}[^>]*>(.*?)</{tag}>", page)
        if m:
            text = strip_html(m.group(1))
            if text:
                return text[:_MAX_BODY_CHARS]
    return strip_html(page)[:_MAX_BODY_CHARS]
