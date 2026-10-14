"""Personio adapter: per-tenant public jobs XML, no key.

Claims `<slug>.jobs.personio.(de|com)` (anchored regex, so a URL can't steer the
fetch) and reads `/xml` with ElementTree, which handles a literal `</position>`
inside a description. Ported from career-ops `providers/personio.mjs` (MIT)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405  see rss.py justification (bounded, no external entities)
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError
from .base import detect_url

ID = "personio"
LABEL = "Personio"
KIND = "ats"
INLINE_DESCRIPTION = True

_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.jobs\.personio\.(de|com)$")


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host if _HOST_RE.match(host) else ""


def _text(el: ET.Element | None) -> str:
    return el.text.strip() if el is not None and el.text else ""


def _job_descriptions_text(position: ET.Element) -> str:
    """Concatenate every jobDescription value into one plain-text body."""
    parts: list[str] = []
    for jd in position.iter("jobDescription"):
        value = jd.find("value")
        if value is not None and value.text:
            parts.append(value.text)
    return strip_html("\n".join(parts)) if parts else ""


def detect(entry: SourceEntry) -> str:
    return detect_url(entry, ID, _host)


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    host = _host(entry.url)
    if not host:
        raise ScraperError(ID, f"cannot extract a personio tenant host from {entry.url}")
    text = fetcher.get_text(f"https://{host}/xml")
    try:
        root = ET.fromstring(text)  # noqa: S314  bounded response, expat resolves no external entities
    except ET.ParseError as e:
        raise ScraperError(ID, f"could not parse personio feed for {host}: {e}") from e

    slug = host.split(".", 1)[0]
    company = entry.company or slug
    jobs: list[NormalizedJob] = []
    for position in root.iter("position"):
        title = _text(position.find("name"))
        job_id = _text(position.find("id"))
        if not title or not re.fullmatch(r"\d+", job_id):
            continue  # need a clean numeric id to build the canonical URL
        offices: list[str] = []
        for office in position.iter("office"):
            name = _text(office)
            if name and name not in offices:
                offices.append(name)
        jobs.append(
            NormalizedJob(
                title=title,
                canonical_url=f"https://{host}/job/{job_id}",
                company=company,
                location=", ".join(offices),
                description=_job_descriptions_text(position),
                posted_at=_text(position.find("createdAt")),
                source_adapter=ID,
            )
        )
    return jobs
