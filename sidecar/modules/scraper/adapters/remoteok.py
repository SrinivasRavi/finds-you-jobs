"""RemoteOK adapter — public jobs API (no auth, no key).

Claims `board = "remoteok"` and `remoteok.com` / `remoteok.io` URLs. One
request: `GET remoteok.com/api` returns a JSON list whose first element is a
legal notice (their ToS asks for a link back — every normalized row keeps the
original posting URL, which satisfies it). The list payload carries description
and salary free, so per the adapter contract we never fetch per job.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "remoteok"
_CLAIM = "remoteok.com"
_HOSTS = {"remoteok.com", "www.remoteok.com", "remoteok.io"}
_API = "https://remoteok.com/api"


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.board == ID:
        return _CLAIM
    host = urlsplit(entry.url).netloc.lower() if entry.url else ""
    return _CLAIM if host in _HOSTS else ""


def _salary(raw: dict) -> str:
    try:
        smin = int(raw.get("salary_min") or 0)
        smax = int(raw.get("salary_max") or 0)
    except (TypeError, ValueError):
        return ""
    if smin <= 0:
        return ""
    return f"${smin:,}–${smax:,}" if smax > smin else f"${smin:,}+"


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    payload = fetcher.get_json(_API)
    if not isinstance(payload, list):
        got = type(payload).__name__
        raise ScraperError(ID, f"unexpected payload shape: expected a JSON list, got {got}")

    jobs: list[NormalizedJob] = []
    for raw in payload:
        # Skips the element-0 legal notice naturally (no position/url keys).
        if not isinstance(raw, dict) or "position" not in raw or "url" not in raw:
            continue
        jobs.append(
            NormalizedJob(
                title=str(raw.get("position") or ""),
                canonical_url=str(raw.get("url") or ""),
                company=entry.company or str(raw.get("company") or ""),
                location=str(raw.get("location") or ""),
                posted_at=str(raw.get("date") or ""),
                description=strip_html(str(raw.get("description") or "")),
                salary=_salary(raw),
                source_adapter=ID,
            )
        )
    return jobs
