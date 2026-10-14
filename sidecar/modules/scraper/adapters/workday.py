"""Workday adapter: public CxS JSON API, no key.

Claims `{tenant}.wd{N}.myworkdayjobs.com/...{site}` URLs. The list answers only
POST `/wday/cxs/{tenant}/{site}/jobs`, capped at 20 per page, so it paginates up
to `MAX_PAGES`. `fetch_detail` fills the JD. `postedOn` is relative text;
parseable forms become a date, "30+ Days Ago" stays empty. career-ops's MIT
provider as behavioral reference."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher, paced_pages
from ..types import NormalizedJob, ScraperError
from .base import path_segments

ID = "workday"
LABEL = "Workday"
KIND = "ats"

_HOST_RE = re.compile(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$")
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_POSTED_RE = re.compile(r"posted\s+(\d+)\s+days?\s+ago", re.IGNORECASE)

# The CxS server caps limit at 20; MAX_PAGES bounds the per-source request
# budget (20 × 10 = 200 rows — beyond that a tenant needs its own filters).
_PAGE_SIZE = 20
MAX_PAGES = 10


def _parse_site(url: str) -> tuple[str, str, str]:
    """(host, tenant, site) from a career-site URL, or ("", "", "")."""
    host = urlsplit(url).netloc.lower()
    m = _HOST_RE.match(host)
    if not m:
        return "", "", ""
    segments = path_segments(url)
    # Skip a leading locale segment (en-US, fr-CA, …); ignore CxS/API paths.
    if segments and _LOCALE_RE.match(segments[0]):
        segments = segments[1:]
    if not segments or segments[0] == "wday":
        return "", "", ""
    return host, m.group(1), segments[0]


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if not entry.url:
        return ""
    host, tenant, site = _parse_site(entry.url)
    return f"{tenant}/{site}" if host else ""


def _posted_iso(text: str, today: datetime | None = None) -> str:
    """Relative `postedOn` → ISO date for the parseable forms, else ""."""
    low = (text or "").strip().lower()
    if not low:
        return ""
    now = today or datetime.now(UTC)
    if "today" in low:
        return now.date().isoformat()
    if "yesterday" in low:
        return (now - timedelta(days=1)).date().isoformat()
    if "+" in low:  # "Posted 30+ Days Ago" — a floor, not a date
        return ""
    m = _POSTED_RE.search(low)
    if m:
        return (now - timedelta(days=int(m.group(1)))).date().isoformat()
    return ""


def fetch_detail(job: NormalizedJob, fetcher: Fetcher) -> str:
    """The posting's JD from the public CxS detail endpoint (approved-plan #8)
    — `GET /wday/cxs/{tenant}/{site}{externalPath}` →
    `jobPostingInfo.jobDescription` (HTML). "" when the shape is unexpected."""
    from ..htmltext import strip_html

    host = urlsplit(job.canonical_url).netloc.lower()
    m = _HOST_RE.match(host)
    if not m:
        return ""
    segments = path_segments(job.canonical_url)
    if len(segments) < 2:
        return ""
    site, external_path = segments[0], "/" + "/".join(segments[1:])
    payload = fetcher.get_json(f"https://{host}/wday/cxs/{m.group(1)}/{site}{external_path}")
    if isinstance(payload, dict):
        info = payload.get("jobPostingInfo")
        if isinstance(info, dict):
            return strip_html(str(info.get("jobDescription") or ""))
    return ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    host, tenant, site = _parse_site(entry.url)
    if not host:
        raise ScraperError(ID, f"cannot extract tenant/site from {entry.url}")
    endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"

    jobs: list[NormalizedJob] = []
    offset = 0
    for _page in paced_pages(range(MAX_PAGES)):
        payload = fetcher.post_json(
            endpoint,
            {"limit": _PAGE_SIZE, "offset": offset, "searchText": "", "appliedFacets": {}},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
            raise ScraperError(
                ID, f"unexpected payload shape from {tenant}/{site}: no jobPostings[] list"
            )
        postings = payload["jobPostings"]
        for raw in postings:
            if not isinstance(raw, dict):
                continue
            external_path = str(raw.get("externalPath") or "")
            if not external_path:
                continue
            jobs.append(
                NormalizedJob(
                    title=str(raw.get("title") or ""),
                    canonical_url=f"https://{host}/{site}{external_path}",
                    company=entry.company or tenant,
                    location=str(raw.get("locationsText") or ""),
                    posted_at=_posted_iso(str(raw.get("postedOn") or "")),
                    description="",  # not in the list payload; quality flags it
                    source_adapter=ID,
                )
            )
        offset += len(postings)
        total = payload.get("total")
        if not postings or (isinstance(total, int) and offset >= total):
            break
    return jobs
