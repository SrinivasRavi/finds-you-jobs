"""BambooHR adapter — public careers JSON API (no auth, no key).

Claims `{company}.bamboohr.com` careers URLs. One request:
`GET {company}.bamboohr.com/careers/list` → `{"result": [...]}` with per-row
`id`, `jobOpeningName`, `departmentLabel`, `location {city, state}`,
`isRemote`. No JD body in the list payload (`description=""`, quality flags
it) and no posted date at all — BambooHR simply doesn't expose one here.
Posting URL: `{company}.bamboohr.com/careers/{id}`.

Re-derived from the public payload shape; career-ops's MIT provider is the
behavioral reference (no code copied — see THIRD_PARTY_NOTICES.md).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "bamboohr"

_SUFFIX = ".bamboohr.com"
_NOT_TENANTS = {"www", "api", "app", "help", "status"}


def _tenant(url: str) -> str:
    host = urlsplit(url).netloc.lower() if url else ""
    if not host.endswith(_SUFFIX):
        return ""
    sub = host[: -len(_SUFFIX)]
    return "" if (not sub or "." in sub or sub in _NOT_TENANTS) else sub


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
                description="",  # not in the list payload; quality flags it
                source_adapter=ID,
            )
        )
    return jobs
