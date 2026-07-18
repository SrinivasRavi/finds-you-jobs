"""Arbeitnow adapter — public job-board JSON API (no auth, no key).

Claims `board = "arbeitnow"` and `arbeitnow.com` URLs. One request:
`GET arbeitnow.com/api/job-board-api` → `{"data": [...]}` with `title`,
`company_name`, `location`, `remote`, `url`, `description` (HTML, carried in
the list payload — no per-job fetch) and `created_at` (unix seconds → ISO).
A remote row gets ", Remote" appended to its location so the shared location
filters can match it.

Re-derived from the public payload shape (JustHireMe carries the same keyless
board — behavioral precedent only, no code seen or copied).
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "arbeitnow"
_CLAIM = "arbeitnow.com"
_HOSTS = {"arbeitnow.com", "www.arbeitnow.com"}
_API = "https://www.arbeitnow.com/api/job-board-api"


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.board == ID:
        return _CLAIM
    host = urlsplit(entry.url).netloc.lower() if entry.url else ""
    return _CLAIM if host in _HOSTS else ""


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
