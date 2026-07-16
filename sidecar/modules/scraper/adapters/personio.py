"""Personio adapter — per-tenant public jobs XML (no auth, no key).

Common across DACH/EU companies: `https://<slug>.jobs.personio.(de|com)/xml`.
Auto-detects a `<slug>.jobs.personio.(de|com)` careers host and normalizes to
the `/xml` feed. Host is validated by an anchored regex (per-tenant subdomains
are the variable part), so an untrusted careers URL can never steer the fetch.

Parsed with ElementTree — which natively handles the `</position>`-inside-a-
description truncation that career-ops's regex parser has to strip around
(free-text job bodies can contain a literal `</position>`). The feed root is
`<workzag-jobs>` with `<position>` children.

Ported from career-ops `providers/personio.mjs` (MIT) — see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405  see rss.py justification (bounded, no external entities)
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "personio"

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
    if entry.type and entry.type != ID:
        return ""
    if entry.type == ID and not entry.url:
        return ""
    return _host(entry.url) if entry.url else ""


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
