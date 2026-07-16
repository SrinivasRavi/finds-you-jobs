"""Teamtailor adapter — per-tenant public jobs RSS (no auth, no key).

Every Teamtailor career site exposes a zero-auth feed at
`https://<slug>.teamtailor.com/jobs.rss`. This adapter auto-detects any
`*.teamtailor.com` careers URL and normalizes it to that feed — so the user
pastes the human careers URL, not the raw feed path (the value over the generic
`rss` adapter, which needs the exact `.rss` URL + `type = "rss"`).

Host is pinned to `*.teamtailor.com` (anchored regex) so an untrusted careers
URL can never steer the fetch elsewhere. Parsed with ElementTree (same as the
`rss` adapter) — no new dependency.

Ported from career-ops `providers/teamtailor.mjs` (MIT) — see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405  see rss.py justification (bounded, no external entities)
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "teamtailor"

_HOST_RE = re.compile(r"^([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.teamtailor\.com$")


def _slug(url: str) -> str:
    m = _HOST_RE.match(urlsplit(url).netloc.lower())
    return m.group(1) if m else ""


def _text(el: ET.Element | None) -> str:
    return el.text.strip() if el is not None and el.text else ""


def _local(el: ET.Element, name: str) -> ET.Element | None:
    """First child whose tag local-name equals `name` (namespace-agnostic)."""
    for child in el:
        if child.tag.rsplit("}", 1)[-1] == name:
            return child
    return None


def _rfc822(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return ""


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.type == ID and not entry.url:
        return ""
    return _slug(entry.url) if entry.url else ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    slug = _slug(entry.url)
    if not slug:
        raise ScraperError(ID, f"cannot extract a teamtailor tenant from {entry.url}")
    text = fetcher.get_text(f"https://{slug}.teamtailor.com/jobs.rss")
    try:
        root = ET.fromstring(text)  # noqa: S314  bounded response, expat resolves no external entities
    except ET.ParseError as e:
        raise ScraperError(ID, f"could not parse teamtailor feed for {slug}: {e}") from e

    company = entry.company or slug
    jobs: list[NormalizedJob] = []
    for item in root.iter("item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        if not title or not link:
            continue
        loc = _local(item, "location")
        if loc is None:
            loc = _local(item, "region")
        jobs.append(
            NormalizedJob(
                title=title,
                canonical_url=link,
                company=company,
                location=_text(loc),
                description=strip_html(_text(item.find("description"))),
                posted_at=_rfc822(_text(item.find("pubDate"))),
                source_adapter=ID,
            )
        )
    return jobs
