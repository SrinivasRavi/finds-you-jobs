"""Pasted-link probe: resolve 1 pasted job URL to a `NormalizedJob` (US-JB-07).

If an adapter claims the URL, ask it via `fetch_posting`, else read its board and
pick the matching row (Greenhouse, Lever and Ashby come back structured).
Otherwise fetch the page and extract a best-effort title and body. The caller
passes the 20 s timeout; no retry; a failure raises `ScraperError` and the user
fills fields by hand. Nothing is persisted here."""

from __future__ import annotations

import re
from collections.abc import Callable

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
        matched = _from_adapter(adapter, entry, fetcher, canonical)
        if matched is not None:
            return matched
        # The board claimed the URL but it wasn't a specific job we could match
        # (a board root, a shape the list doesn't expose, or a list too big to
        # fetch at all) — fall through to generic extraction so the user still
        # gets a pre-filled draft.

    return _generic_probe(url, canonical, fetcher)


def _from_adapter(
    adapter: object, entry: SourceEntry, fetcher: Fetcher, canonical: str
) -> NormalizedJob | None:
    """The claiming adapter's answer for 1 pasted URL, or None. A ScraperError is
    never fatal: the generic path still produces a draft."""
    fetch_posting: Callable[[str, Fetcher], NormalizedJob | None] | None = getattr(
        adapter, "fetch_posting", None
    )
    if fetch_posting is not None:
        try:
            job = fetch_posting(entry.url, fetcher)
        except ScraperError:
            job = None
        if job is not None:
            job.canonical_url = canonical
            return job
    try:
        return _match_from_board(adapter, entry, fetcher, canonical)
    except ScraperError:
        return None


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
