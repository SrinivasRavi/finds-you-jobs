"""Workday adapter — public CxS JSON API (no auth, no key).

Claims `{tenant}.wd{N}.myworkdayjobs.com/...{site}` career-site URLs (with or
without a locale segment like `en-US`). The public list endpoint answers only
to POST:

    POST https://{host}/wday/cxs/{tenant}/{site}/jobs
    {"limit": 20, "offset": N, "searchText": "", "appliedFacets": {}}

→ `{"total": T, "jobPostings": [{title, externalPath, locationsText,
postedOn, ...}]}`. The server caps `limit` at 20, so unlike every GET adapter
this one paginates — bounded at `MAX_PAGES` requests per scan (the fetcher
counts each into the source's Usage; the per-source request budget stays
visible, discovery-expansion decision 2026-07-17).

Honest field notes: the list payload has no JD body (per-job fetch
deliberately avoided — SmartRecruiters/Recruitee precedent, `description=""`
and quality flags it) and `postedOn` is relative human text ("Posted Today",
"Posted 3 Days Ago"); the parseable forms convert to an ISO date, "30+ Days
Ago" honestly stays empty rather than guessing.

Re-derived from the public CxS payload shape; career-ops's MIT provider is
the behavioral reference (no code copied — see THIRD_PARTY_NOTICES.md).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "workday"

_HOST_RE = re.compile(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$")
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_POSTED_RE = re.compile(r"posted\s+(\d+)\s+days?\s+ago", re.IGNORECASE)

# The CxS server caps limit at 20; MAX_PAGES bounds the per-source request
# budget (20 × 10 = 200 rows — beyond that a tenant needs its own filters).
_PAGE_SIZE = 20
MAX_PAGES = 10


def _parse_site(url: str) -> tuple[str, str, str]:
    """(host, tenant, site) from a career-site URL, or ("", "", "")."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    m = _HOST_RE.match(host)
    if not m:
        return "", "", ""
    segments = [s for s in parts.path.split("/") if s]
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

    parts = urlsplit(job.canonical_url)
    host = parts.netloc.lower()
    m = _HOST_RE.match(host)
    if not m:
        return ""
    segments = [s for s in parts.path.split("/") if s]
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
    for _page in range(MAX_PAGES):
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
