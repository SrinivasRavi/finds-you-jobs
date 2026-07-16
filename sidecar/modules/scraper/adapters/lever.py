"""Lever adapter — public postings JSON API (no auth, no key).

Claims `jobs.lever.co/<slug>` (+ the `jobs.eu.lever.co` EU variant). One request
per board: `GET api.lever.co/v0/postings/<slug>?mode=json` (EU host →
`api.eu.lever.co`). The list payload ships `descriptionPlain` free, so — per the
adapter contract — we take the description from the list and never fetch per job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "lever"

_HOSTS = {
    "jobs.lever.co": "",
    "jobs.eu.lever.co": "eu.",
}


def _slug_and_region(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host not in _HOSTS:
        return "", ""
    segments = [s for s in parts.path.split("/") if s]
    return (segments[0], _HOSTS[host]) if segments else ("", "")


def _posted_at(raw: dict) -> str:
    created = raw.get("createdAt")
    if not isinstance(created, int):
        return ""
    return datetime.fromtimestamp(created / 1000, tz=UTC).isoformat()


def _location(categories: dict) -> str:
    location = str(categories.get("location") or "")
    if location:
        return location
    all_locations = categories.get("allLocations")
    if isinstance(all_locations, list) and all_locations:
        return "; ".join(str(x) for x in all_locations if x)
    return ""


def _salary(raw: dict) -> str:
    salary_range = raw.get("salaryRange")
    if not isinstance(salary_range, dict):
        return ""
    lo = salary_range.get("min")
    hi = salary_range.get("max")
    if lo is None or hi is None:
        return ""
    currency = str(salary_range.get("currency") or "")
    return f"{lo}–{hi} {currency}".strip()


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.type == ID and not entry.url:
        return ""
    slug, _ = _slug_and_region(entry.url) if entry.url else ("", "")
    return slug


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    slug, region = _slug_and_region(entry.url)
    if not slug:
        raise ScraperError(ID, f"cannot extract a board slug from {entry.url}")
    payload = fetcher.get_json(f"https://api.{region}lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(payload, list):
        raise ScraperError(ID, f"unexpected payload shape from board {slug}: not a list")

    jobs: list[NormalizedJob] = []
    for raw in payload:
        categories = raw.get("categories") or {}
        jobs.append(
            NormalizedJob(
                title=str(raw.get("text") or ""),
                canonical_url=str(raw.get("hostedUrl") or ""),
                company=entry.company or str(raw.get("company") or "") or slug,
                location=_location(categories),
                description=str(raw.get("descriptionPlain") or ""),
                posted_at=_posted_at(raw),
                salary=_salary(raw),
                source_adapter=ID,
            )
        )
    return jobs
