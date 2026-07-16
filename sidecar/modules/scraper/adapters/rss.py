"""RSS / Atom adapter — generic feed fallback (registered last).

Claims any `type = "rss"` source, or a URL whose path ends `.rss`/`.xml`/
`.atom` or contains `/rss`/`/feed`; the source key is the feed host. One
request: `GET <url>`, parsed as RSS 2.0 (`channel/item`) or Atom
(`feed/entry`). We Work Remotely feeds carry a `<region>` child (mapped to
location) and `Company: Role` titles (split into company/title).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405  see fromstring justification below
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "rss"
_ATOM = "http://www.w3.org/2005/Atom"
_FEED_SUFFIXES = (".rss", ".xml", ".atom")


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if not entry.url:
        return ""
    parts = urlsplit(entry.url)
    host = parts.netloc.lower()
    if entry.type == ID:
        return host
    path = parts.path.lower()
    if path.endswith(_FEED_SUFFIXES) or "/rss" in path or "/feed" in path:
        return host
    return ""


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _rfc822(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return ""


def _split_title(title: str) -> tuple[str, str]:
    if title.count(": ") == 1:
        left, right = title.split(": ", 1)
        if len(left) <= 60:
            return left, right
    return "", title


def _atom_link(entry_el: ET.Element) -> str:
    links = entry_el.findall(f"{{{_ATOM}}}link")
    for link in links:
        if link.get("rel") == "alternate" and link.get("href"):
            return link.get("href", "")
    for link in links:
        if link.get("href"):
            return link.get("href", "")
    return ""


def _parse_rss(root: ET.Element) -> list[NormalizedJob]:
    jobs: list[NormalizedJob] = []
    for item in root.iter("item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        if not link:
            guid = _text(item.find("guid"))
            if guid.startswith("http"):
                link = guid
        if not link or not title:
            continue
        company, title = _split_title(title)
        jobs.append(
            NormalizedJob(
                title=title,
                canonical_url=link,
                company=company,
                location=_text(item.find("region")),
                description=strip_html(_text(item.find("description"))),
                posted_at=_rfc822(_text(item.find("pubDate"))),
                salary="",
                source_adapter=ID,
            )
        )
    return jobs


def _parse_atom(root: ET.Element) -> list[NormalizedJob]:
    jobs: list[NormalizedJob] = []
    for entry_el in root.findall(f"{{{_ATOM}}}entry"):
        title = _text(entry_el.find(f"{{{_ATOM}}}title"))
        link = _atom_link(entry_el)
        if not link or not title:
            continue
        company, title = _split_title(title)
        description = _text(entry_el.find(f"{{{_ATOM}}}summary")) or _text(
            entry_el.find(f"{{{_ATOM}}}content")
        )
        posted = _text(entry_el.find(f"{{{_ATOM}}}published")) or _text(
            entry_el.find(f"{{{_ATOM}}}updated")
        )
        jobs.append(
            NormalizedJob(
                title=title,
                canonical_url=link,
                company=company,
                location="",
                description=strip_html(description),
                posted_at=posted,
                salary="",
                source_adapter=ID,
            )
        )
    return jobs


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    if not entry.url:
        raise ScraperError(ID, "rss source has no url")
    text = fetcher.get_text(entry.url)
    try:
        # User-configured feed sources; response size is capped by the fetcher and
        # stdlib expat does not resolve external entities — so untrusted-XML risk
        # is bounded. S314/S405 acknowledged.
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError as e:
        raise ScraperError(ID, f"could not parse XML feed {entry.url}: {e}") from e
    if root.tag == f"{{{_ATOM}}}feed":
        return _parse_atom(root)
    return _parse_rss(root)
