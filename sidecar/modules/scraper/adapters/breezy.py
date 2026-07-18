"""Breezy adapter — public positions JSON API (no auth, no key).

Claims `{company}.breezy.hr` URLs. One request: `GET {company}.breezy.hr/json`
→ a JSON list of open positions with `name`, `url` (full posting URL),
`published_date`, and a nested `location {city, state {name}, country {name}}`
(live-verified 2026-07-18 against forge-nano/compass-datacenters — there is no
flat `location.name`; some tenants may still send one, kept as the preferred
fallback). No JD body in the list payload (`description=""`, quality flags it).

Re-derived from the public payload shape; career-ops's MIT provider is the
behavioral reference (no code copied — see THIRD_PARTY_NOTICES.md).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "breezy"

_SUFFIX = ".breezy.hr"
_NOT_TENANTS = {"www", "app", "api", "help"}


def _tenant(url: str) -> str:
    host = urlsplit(url).netloc.lower() if url else ""
    if not host.endswith(_SUFFIX):
        return ""
    sub = host[: -len(_SUFFIX)]
    return "" if (not sub or "." in sub or sub in _NOT_TENANTS) else sub


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    return _tenant(entry.url)


def _location(raw: dict) -> str:
    location = raw.get("location")
    if not isinstance(location, dict):
        return ""
    name = str(location.get("name") or "")
    if name:
        return name
    parts: list[str] = [str(location.get("city") or "").strip()]
    for key in ("state", "country"):
        nested = location.get(key)
        if isinstance(nested, dict):
            parts.append(str(nested.get("name") or "").strip())
    return ", ".join(p for p in parts if p)


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    tenant = _tenant(entry.url)
    if not tenant:
        raise ScraperError(ID, f"cannot extract a company subdomain from {entry.url}")
    payload = fetcher.get_json(f"https://{tenant}.breezy.hr/json")
    if not isinstance(payload, list):
        got = type(payload).__name__
        raise ScraperError(ID, f"unexpected payload shape: expected a JSON list, got {got}")

    jobs: list[NormalizedJob] = []
    for raw in payload:
        if not isinstance(raw, dict) or not raw.get("url"):
            continue
        jobs.append(
            NormalizedJob(
                title=str(raw.get("name") or ""),
                canonical_url=str(raw.get("url") or ""),
                company=entry.company or tenant,
                location=_location(raw),
                posted_at=str(raw.get("published_date") or ""),
                description="",  # not in the list payload; quality flags it
                source_adapter=ID,
            )
        )
    return jobs
