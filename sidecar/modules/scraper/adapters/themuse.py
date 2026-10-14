"""The Muse adapter: public jobs JSON API, no key.

Claims `board = "themuse"` and themuse.com URLs. Pages about 20 rows up to
`MAX_PAGES`; the JD is in the list. Re-derived from the payload (JustHireMe as
precedent only)."""

from __future__ import annotations

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher, paced_pages
from ..types import NormalizedJob, ScraperError
from .base import detect_board

ID = "themuse"
LABEL = "The Muse"
KIND = "board"
INLINE_DESCRIPTION = True
_CLAIM = "themuse.com"
_HOSTS = {"themuse.com", "www.themuse.com"}
_API = "https://www.themuse.com/api/public/jobs"

MAX_PAGES = 3  # ~60 rows; a keyword board, not a per-company feed


def detect(entry: SourceEntry) -> str:
    return detect_board(entry, ID, _CLAIM, _HOSTS)


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    jobs: list[NormalizedJob] = []
    for page in paced_pages(range(1, MAX_PAGES + 1)):
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
