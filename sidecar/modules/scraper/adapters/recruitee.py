"""Recruitee adapter — public per-tenant offers API (no auth, no key).

Claims `<slug>.recruitee.com`. One request per tenant:
`GET https://<slug>.recruitee.com/api/offers/`. Per-tenant subdomains are the
variable part, so the host is validated by an anchored regex rather than a
static allowlist (the SSRF stance career-ops uses for the same source).

Ported from career-ops `providers/recruitee.mjs` (MIT) — see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "recruitee"

_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.recruitee\.com$")


def _iso_date(value: object) -> str:
    """Recruitee stamps `published_at` as `YYYY-MM-DD HH:MM:SS UTC` (not ISO), which
    the freshness filter can't parse — normalize it to ISO 8601. Passes through a
    value that's already ISO (or unrecognised, best-effort)."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S UTC").isoformat() + "+00:00"
    except ValueError:
        return raw


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host if _HOST_RE.match(host) else ""


def _location(raw: dict) -> str:
    explicit = raw.get("location")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    remote = "Remote" if raw.get("remote") else ""
    return ", ".join(
        str(p) for p in (raw.get("city"), raw.get("country"), remote) if p
    )


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.type == ID and not entry.url:
        return ""
    return _host(entry.url) if entry.url else ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    host = _host(entry.url)
    if not host:
        raise ScraperError(ID, f"cannot extract a recruitee tenant host from {entry.url}")
    payload = fetcher.get_json(f"https://{host}/api/offers/")
    if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
        raise ScraperError(ID, f"unexpected payload shape from {host}: no offers[] list")

    slug = host.split(".", 1)[0]
    jobs: list[NormalizedJob] = []
    for raw in payload["offers"]:
        if not isinstance(raw, dict):
            continue
        jobs.append(
            NormalizedJob(
                title=str(raw.get("title") or ""),
                canonical_url=str(raw.get("careers_url") or raw.get("url") or ""),
                company=entry.company or slug,
                location=_location(raw),
                description="",  # offers list carries no clean plain-text body
                posted_at=_iso_date(raw.get("published_at") or raw.get("created_at")),
                source_adapter=ID,
            )
        )
    return jobs
