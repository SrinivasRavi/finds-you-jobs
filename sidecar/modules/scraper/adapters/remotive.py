"""Remotive adapter — public remote-jobs API (no auth, no key).

Claims `board = "remotive"` and `remotive.com` / `remotive.io` URLs. One
request: `GET remotive.com/api/remote-jobs` returns `{jobs: [...]}` with
description and a free-text salary carried inline, so per the adapter contract
we never fetch per job.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "remotive"
_CLAIM = "remotive.com"
_HOSTS = {"remotive.com", "www.remotive.com", "remotive.io"}
_API = "https://remotive.com/api/remote-jobs"


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.board == ID:
        return _CLAIM
    host = urlsplit(entry.url).netloc.lower() if entry.url else ""
    return _CLAIM if host in _HOSTS else ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    payload = fetcher.get_json(_API)
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ScraperError(ID, "unexpected payload shape: no jobs[] list")

    jobs: list[NormalizedJob] = []
    for raw in payload["jobs"]:
        if not isinstance(raw, dict):
            continue
        jobs.append(
            NormalizedJob(
                title=str(raw.get("title") or ""),
                canonical_url=str(raw.get("url") or ""),
                company=entry.company or str(raw.get("company_name") or ""),
                location=str(raw.get("candidate_required_location") or ""),
                posted_at=str(raw.get("publication_date") or ""),
                description=strip_html(str(raw.get("description") or "")),
                salary=str(raw.get("salary") or ""),
                source_adapter=ID,
            )
        )
    return jobs
