"""Arbeitnow adapter: public job-board JSON API, no key.

Claims `board = "arbeitnow"` and arbeitnow.com URLs. 1 request to
`/api/job-board-api`; the JD is in the list; remote rows get ", Remote"
appended for the location filters. Re-derived from the payload (JustHireMe as
precedent only)."""

from __future__ import annotations

from datetime import UTC, datetime

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError
from .base import detect_board

ID = "arbeitnow"
LABEL = "Arbeitnow"
KIND = "board"
INLINE_DESCRIPTION = True
_CLAIM = "arbeitnow.com"
_HOSTS = {"arbeitnow.com", "www.arbeitnow.com"}
_API = "https://www.arbeitnow.com/api/job-board-api"


def detect(entry: SourceEntry) -> str:
    return detect_board(entry, ID, _CLAIM, _HOSTS)


def _posted_iso(created_at: object) -> str:
    try:
        ts = int(created_at)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()


def _location(raw: dict) -> str:
    location = str(raw.get("location") or "")
    if raw.get("remote") and "remote" not in location.lower():
        return f"{location}, Remote" if location else "Remote"
    return location


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    payload = fetcher.get_json(_API)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ScraperError(ID, "unexpected payload shape: no data[] list")

    jobs: list[NormalizedJob] = []
    for raw in payload["data"]:
        if not isinstance(raw, dict) or not raw.get("url"):
            continue
        jobs.append(
            NormalizedJob(
                title=str(raw.get("title") or ""),
                canonical_url=str(raw.get("url") or ""),
                company=entry.company or str(raw.get("company_name") or ""),
                location=_location(raw),
                posted_at=_posted_iso(raw.get("created_at")),
                description=strip_html(str(raw.get("description") or "")),
                source_adapter=ID,
            )
        )
    return jobs
