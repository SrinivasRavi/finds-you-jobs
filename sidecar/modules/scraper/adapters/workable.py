"""Workable adapter — public account widget API (no auth, no key).

Claims `apply.workable.com/<slug>` (rejecting the API/job path prefixes `api`
and `j`). One request per board:
`GET apply.workable.com/api/v1/widget/accounts/<slug>?details=true`. The
`details=true` flag makes the widget list payload carry each posting's full JD
body (HTML in `description`) in the *same* request — description free, still one
list request, never per-job (maintainer decision 2026-07-07).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "workable"

_HOST = "apply.workable.com"
_RESERVED = {"api", "j"}


def _slug(url: str) -> str:
    parts = urlsplit(url)
    if parts.netloc.lower() != _HOST:
        return ""
    segments = [s for s in parts.path.split("/") if s]
    if not segments or segments[0] in _RESERVED:
        return ""
    return segments[0]


def _location(raw: dict) -> str:
    rest = ", ".join(
        str(raw.get(k) or "") for k in ("city", "state", "country") if raw.get(k)
    )
    if raw.get("telecommuting"):
        return f"Remote; {rest}" if rest else "Remote"
    return rest


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.type == ID and not entry.url:
        return ""
    return _slug(entry.url) if entry.url else ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    slug = _slug(entry.url)
    if not slug:
        raise ScraperError(ID, f"cannot extract an account slug from {entry.url}")
    payload = fetcher.get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ScraperError(ID, f"unexpected payload shape from account {slug}: no jobs[] list")

    account_name = str(payload.get("name") or "")
    jobs: list[NormalizedJob] = []
    for raw in payload["jobs"]:
        jobs.append(
            NormalizedJob(
                title=str(raw.get("title") or ""),
                canonical_url=str(raw.get("url") or raw.get("shortlink") or ""),
                company=entry.company or str(raw.get("company") or "") or account_name or slug,
                location=_location(raw),
                description=strip_html(str(raw.get("description") or "")),
                posted_at=str(raw.get("published_on") or raw.get("created_at") or ""),
                salary="",
                source_adapter=ID,
            )
        )
    return jobs
