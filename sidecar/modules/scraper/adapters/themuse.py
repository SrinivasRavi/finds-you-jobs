"""The Muse adapter — public jobs JSON API (no auth, no key).

Claims `board = "themuse"` and `themuse.com` URLs. The API pages at ~20 rows:
`GET themuse.com/api/public/jobs?page=N` → `{"results": [...], "page_count"}`
with `name`, `company {name}`, `locations [{name}]`, `refs {landing_page}`,
`contents` (HTML JD, carried in the list payload) and `publication_date`.
Bounded at `MAX_PAGES` requests per scan (same request-budget discipline as
the Workday adapter; the fetcher counts each call into Usage).

Re-derived from the public payload shape (JustHireMe carries the same keyless
board — behavioral precedent only, no code seen or copied).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "themuse"
_CLAIM = "themuse.com"
_HOSTS = {"themuse.com", "www.themuse.com"}
_API = "https://www.themuse.com/api/public/jobs"

MAX_PAGES = 3  # ~60 rows; a keyword board, not a per-company feed


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.board == ID:
        return _CLAIM
    host = urlsplit(entry.url).netloc.lower() if entry.url else ""
    return _CLAIM if host in _HOSTS else ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    jobs: list[NormalizedJob] = []
    for page in range(1, MAX_PAGES + 1):
        payload = fetcher.get_json(f"{_API}?page={page}")
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ScraperError(ID, f"unexpected payload shape on page {page}: no results[]")
        results = payload["results"]
        for raw in results:
            if not isinstance(raw, dict):
                continue
            refs = raw.get("refs") or {}
            url = str(refs.get("landing_page") or "") if isinstance(refs, dict) else ""
            if not url:
                continue
            company = raw.get("company") or {}
            locations = raw.get("locations") or []
            names = [
                str(loc.get("name") or "")
                for loc in locations
                if isinstance(loc, dict) and loc.get("name")
            ]
            jobs.append(
                NormalizedJob(
                    title=str(raw.get("name") or ""),
                    canonical_url=url,
                    company=entry.company
                    or (str(company.get("name") or "") if isinstance(company, dict) else ""),
                    location=", ".join(names),
                    posted_at=str(raw.get("publication_date") or ""),
                    description=strip_html(str(raw.get("contents") or "")),
                    source_adapter=ID,
                )
            )
        page_count = payload.get("page_count")
        if not results or (isinstance(page_count, int) and page >= page_count):
            break
    return jobs
