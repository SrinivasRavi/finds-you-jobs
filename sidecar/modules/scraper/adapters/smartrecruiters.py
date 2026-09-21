"""SmartRecruiters adapter — public postings API (no auth, no key).

Claims `careers.smartrecruiters.com/<slug>` / `jobs.smartrecruiters.com/<slug>`.
Postings come from `api.smartrecruiters.com/v1/companies/<slug>/postings`, which
caps at 100 rows/page, so this adapter pages with a bounded loop (unlike the
single-request ATS adapters). The `ref` on each posting is the API URL; we
rewrite it to the public `jobs.smartrecruiters.com/<slug>/postings/<id>`
careers URL — the list payload carries no JD body, so `fetch_detail` fills it
per row from the single-posting API (approved-plan #8).

Ported from career-ops `providers/smartrecruiters.mjs` (MIT) — see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher, paced_pages
from ..types import NormalizedJob, ScraperError
from .base import first_path_segment, path_segments

ID = "smartrecruiters"

_CAREERS_HOSTS = {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"}
_API_HOST = "api.smartrecruiters.com"
_PAGE_SIZE = 100
_MAX_PAGES = 50  # safety cap: 5000 postings @ 100/page


def _slug(url: str) -> str:
    if urlsplit(url).netloc.lower() not in _CAREERS_HOSTS:
        return ""
    return first_path_segment(url)


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


def fetch_detail(job: NormalizedJob, fetcher: Fetcher) -> str:
    """The posting's JD from the single-posting API (approved-plan #8) —
    `GET api.smartrecruiters.com/v1/companies/{slug}/postings/{id}` →
    `jobAd.sections`, a dict of named sections each `{title, text}`; text is
    HTML, concatenated in payload order. "" when the URL or shape is
    unexpected."""
    if urlsplit(job.canonical_url).netloc.lower() not in _CAREERS_HOSTS:
        return ""
    segments = path_segments(job.canonical_url)
    if len(segments) < 3 or segments[1] != "postings":
        return ""
    slug, posting_id = segments[0], segments[2]
    payload = fetcher.get_json(f"https://{_API_HOST}/v1/companies/{slug}/postings/{posting_id}")
    if not isinstance(payload, dict):
        return ""
    ad = payload.get("jobAd")
    sections = ad.get("sections") if isinstance(ad, dict) else None
    if not isinstance(sections, dict):
        return ""
    parts = [
        strip_html(str(section["text"]))
        for section in sections.values()
        if isinstance(section, dict) and section.get("text")
    ]
    return "\n\n".join(parts)


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    slug = _slug(entry.url)
    if not slug:
        raise ScraperError(ID, f"cannot extract a company slug from {entry.url}")

    jobs: list[NormalizedJob] = []
    for page in paced_pages(range(_MAX_PAGES)):
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
                    description="",  # not in the list payload; fetch_detail fills it
                    posted_at=str(raw.get("releasedDate") or raw.get("createdOn") or ""),
                    source_adapter=ID,
                )
            )
        if len(content) < _PAGE_SIZE:
            break  # last (short) page
    return jobs
