"""Ashby adapter — public job-board posting API (no auth, no key).

Claims `jobs.ashbyhq.com/<org>`. One request per board:
`GET api.ashbyhq.com/posting-api/job-board/<org>?includeCompensation=true`. The
list payload ships `descriptionPlain` (and compensation) free, so — per the
adapter contract — we normalize from the list and never fetch per job.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "ashby"

_HOST = "jobs.ashbyhq.com"


def _org(url: str) -> str:
    parts = urlsplit(url)
    if parts.netloc.lower() != _HOST:
        return ""
    segments = [s for s in parts.path.split("/") if s]
    return segments[0] if segments else ""


def _location(raw: dict) -> str:
    parts = [str(raw.get("location") or "")]
    secondary = raw.get("secondaryLocations")
    if isinstance(secondary, list):
        parts.extend(str(s.get("location") or "") for s in secondary if isinstance(s, dict))
    location = "; ".join(p for p in parts if p)
    if raw.get("isRemote") and "remote" not in location.lower():
        return f"Remote; {location}" if location else "Remote"
    return location


def _salary(raw: dict) -> str:
    compensation = raw.get("compensation")
    if not isinstance(compensation, dict):
        return ""
    return str(
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
        or ""
    )


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.type == ID and not entry.url:
        return ""
    return _org(entry.url) if entry.url else ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    org = _org(entry.url)
    if not org:
        raise ScraperError(ID, f"cannot extract a job-board org from {entry.url}")
    payload = fetcher.get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ScraperError(ID, f"unexpected payload shape from board {org}: no jobs[] list")

    jobs: list[NormalizedJob] = []
    for raw in payload["jobs"]:
        if raw.get("isListed") is False:
            continue
        jobs.append(
            NormalizedJob(
                title=str(raw.get("title") or ""),
                canonical_url=str(raw.get("jobUrl") or ""),
                company=entry.company or str(raw.get("company") or "") or org,
                location=_location(raw),
                description=str(raw.get("descriptionPlain") or ""),
                posted_at=str(raw.get("publishedAt") or ""),
                salary=_salary(raw),
                source_adapter=ID,
            )
        )
    return jobs
