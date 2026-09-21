"""BambooHR adapter — public careers JSON API (no auth, no key).

Claims `{company}.bamboohr.com` careers URLs. One request:
`GET {company}.bamboohr.com/careers/list` → `{"result": [...]}` with per-row
`id`, `jobOpeningName`, `departmentLabel`, `location {city, state}`,
`isRemote`. No JD body in the list payload and no posted date at all —
BambooHR simply doesn't expose either here. `fetch_detail` fills the JD from
the per-job endpoint: `GET {company}.bamboohr.com/careers/{id}/detail` →
`{"jobOpening": {"description": "<html>"}}`. Posting URL:
`{company}.bamboohr.com/careers/{id}`.

Re-derived from the public payload shape; career-ops's MIT provider is the
behavioral reference (no code copied — see THIRD_PARTY_NOTICES.md).
"""

from __future__ import annotations

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError
from .base import path_segments, subdomain_tenant

ID = "bamboohr"

_SUFFIX = ".bamboohr.com"
_NOT_TENANTS = {"www", "api", "app", "help", "status"}


def _tenant(url: str) -> str:
    return subdomain_tenant(url, _SUFFIX, _NOT_TENANTS)


def fetch_detail(job: NormalizedJob, fetcher: Fetcher) -> str:
    """The posting's JD from the per-job detail endpoint (approved-plan #8)
    — `GET {tenant}.bamboohr.com/careers/{id}/detail` →
    `jobOpening.description` (HTML). The id rides `job.canonical_url` (the
    list payload never stores it) rather than a second field. "" when the
    URL doesn't parse or the shape is unexpected."""
    tenant = _tenant(job.canonical_url)
    segments = path_segments(job.canonical_url)
    if not tenant or len(segments) < 2 or segments[0] != "careers":
        return ""
    payload = fetcher.get_json(f"https://{tenant}.bamboohr.com/careers/{segments[1]}/detail")
    if isinstance(payload, dict):
        opening = payload.get("jobOpening")
        if isinstance(opening, dict):
            return strip_html(str(opening.get("description") or ""))
    return ""


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    return _tenant(entry.url)


def _location(raw: dict) -> str:
    loc = raw.get("location") or {}
    parts = [str(loc.get("city") or "").strip(), str(loc.get("state") or "").strip()]
    text = ", ".join(p for p in parts if p)
    if raw.get("isRemote"):
        return f"{text} (Remote)" if text else "Remote"
    return text


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    tenant = _tenant(entry.url)
    if not tenant:
        raise ScraperError(ID, f"cannot extract a company subdomain from {entry.url}")
    payload = fetcher.get_json(f"https://{tenant}.bamboohr.com/careers/list")
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise ScraperError(ID, f"unexpected payload shape from {tenant}: no result[] list")

    jobs: list[NormalizedJob] = []
    for raw in payload["result"]:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        jobs.append(
            NormalizedJob(
                title=str(raw.get("jobOpeningName") or ""),
                canonical_url=f"https://{tenant}.bamboohr.com/careers/{raw['id']}",
                company=entry.company or tenant,
                location=_location(raw),
                description="",  # not in the list payload; fetch_detail fills it
                source_adapter=ID,
            )
        )
    return jobs
