"""SmartRecruiters adapter — public postings API (no auth, no key).

Claims `careers.smartrecruiters.com/<slug>` / `jobs.smartrecruiters.com/<slug>`.
Postings come from `api.smartrecruiters.com/v1/companies/<slug>/postings`, which
caps at 100 rows/page, so this adapter pages with a bounded loop (unlike the
single-request ATS adapters) — still zero-token, still no per-job fetch. The
`ref` on each posting is the API URL; we rewrite it to the public
`jobs.smartrecruiters.com/<slug>/postings/<id>` careers URL.

Ported from career-ops `providers/smartrecruiters.mjs` (MIT) — see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "smartrecruiters"

_CAREERS_HOSTS = {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"}
_API_HOST = "api.smartrecruiters.com"
_PAGE_SIZE = 100
_MAX_PAGES = 50  # safety cap: 5000 postings @ 100/page


def _slug(url: str) -> str:
    parts = urlsplit(url)
    if parts.netloc.lower() not in _CAREERS_HOSTS:
        return ""
    segments = [s for s in parts.path.split("/") if s]
    return segments[0] if segments else ""


def _postings_url(slug: str, offset: int) -> str:
    return (
        f"https://{_API_HOST}/v1/companies/{slug}/postings"
        f"?limit={_PAGE_SIZE}&offset={offset}&status=PUBLIC"
    )


def _location(raw: dict) -> str:
    loc = raw.get("location")
    if not isinstance(loc, dict):
        return ""
    full = loc.get("fullLocation") or ", ".join(
        str(p) for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p
    )
    # Append "Remote" only when the assembled string doesn't already say so —
    # SmartRecruiters' own fullLocation often bakes REMOTE in (guard mirrors ashby).
    if loc.get("remote") and "remote" not in full.lower():
        return f"{full}, Remote" if full else "Remote"
    return full


def _public_url(raw: dict, slug: str) -> str:
    """Rewrite the API `ref` to the public careers URL; synthesise from id else."""
    ref = raw.get("ref")
    if isinstance(ref, str) and ref:
        parts = urlsplit(ref)
        if (
            parts.scheme == "https"
            and parts.netloc.lower() == _API_HOST
            and parts.path.startswith("/v1/companies/")
        ):
            rest = parts.path[len("/v1/companies/") :]
            return f"https://jobs.smartrecruiters.com/{rest}"
    job_id = str(raw.get("id") or "")
    return f"https://jobs.smartrecruiters.com/{slug}/postings/{job_id}" if job_id else ""


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.type == ID and not entry.url:
        return ""
    return _slug(entry.url) if entry.url else ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    slug = _slug(entry.url)
    if not slug:
        raise ScraperError(ID, f"cannot extract a company slug from {entry.url}")

    jobs: list[NormalizedJob] = []
    for page in range(_MAX_PAGES):
        payload = fetcher.get_json(_postings_url(slug, page * _PAGE_SIZE))
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
            raise ScraperError(ID, f"unexpected payload shape from {slug}: no content[] list")
        content = payload["content"]
        for raw in content:
            if not isinstance(raw, dict):
                continue
            jobs.append(
                NormalizedJob(
                    title=str(raw.get("name") or ""),
                    canonical_url=_public_url(raw, slug),
                    company=entry.company or slug,
                    location=_location(raw),
                    description="",  # not in the list payload; per-job fetch avoided
                    posted_at=str(raw.get("releasedDate") or raw.get("createdOn") or ""),
                    source_adapter=ID,
                )
            )
        if len(content) < _PAGE_SIZE:
            break  # last (short) page
    return jobs
