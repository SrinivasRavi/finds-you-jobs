"""LinkedIn (guest) adapter — the public, no-login job-search endpoint.

This is the *unauthenticated* LinkedIn: the guest job-search API that anyone's
browser hits, no account and no session in the loop — so it carries **none** of
the account-ban risk of the authenticated Voyager path (that path stays behind
the default-off LinkedIn toggle, a separate user-initiated action). The only
exposure here is a temporary per-IP rate-limit, which the run at a single
user's personal desktop volume rarely reaches.

Search shape (adapters/base.py): claims `board = "linkedin"` and builds queries
from the user's role aliases × locations. Endpoint:

    GET .../jobs-guest/jobs/api/seeMoreJobPostings/search
        ?keywords={kw}&location={loc}&start={n}

→ an HTML list of job cards (title, company, location, posting date, job URL).
Browser-standard headers are required (the honest bot UA is refused) — the
header-policy line is documented in `http.BROWSER_HEADERS`. Paginated by `start`
in steps of 25, bounded at `MAX_PAGES` per query for IP courtesy. The card URL
carries tracking params; we normalize to the stable `/jobs/view/{id}` form so
cross-query and cross-source dedup works.

Endpoint shape re-derived from JobSpy's `linkedin` module (MIT) as the
behavioral reference — headers dict and card fields; no code copied. See
UPSTREAMS.md.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import BROWSER_HEADERS, Fetcher
from ..searchquery import build_queries
from ..types import NormalizedJob, ScanPrefs, ScraperError

ID = "linkedin"
_CLAIM = "linkedin"
_BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

_PAGE_SIZE = 25
MAX_PAGES = 2  # ≤50 rows/query; conservative for the guest per-IP rate limit

# Card field extractors — the guest cards are stable, class-named HTML.
_CARD_SPLIT = re.compile(r'<li[ >]')
_URN_RE = re.compile(r"urn:li:jobPosting:(\d+)")
_HREF_RE = re.compile(r'href="(https://[^"]*?/jobs/view/[^"]+)"')
_JOBID_IN_HREF = re.compile(r"/jobs/view/(?:[^/?]*-)?(\d+)")
_TITLE_RE = re.compile(r'class="base-search-card__title"[^>]*>(.*?)</h3>', re.DOTALL)
_COMPANY_RE = re.compile(r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>', re.DOTALL)
_LOCATION_RE = re.compile(r'class="job-search-card__location"[^>]*>(.*?)</span>', re.DOTALL)
_DATE_RE = re.compile(r'datetime="([0-9-]+)"')


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    return _CLAIM if entry.board == ID else ""


def _job_id(card: str) -> str:
    urn = _URN_RE.search(card)
    if urn:
        return urn.group(1)
    href = _HREF_RE.search(card)
    if href:
        m = _JOBID_IN_HREF.search(href.group(1))
        if m:
            return m.group(1)
    return ""


def _field(pattern: re.Pattern[str], card: str) -> str:
    m = pattern.search(card)
    return strip_html(m.group(1)) if m else ""


def _parse_cards(html: str, company_override: str) -> list[NormalizedJob]:
    jobs: list[NormalizedJob] = []
    for card in _CARD_SPLIT.split(html)[1:]:
        job_id = _job_id(card)
        title = _field(_TITLE_RE, card)
        if not job_id or not title:
            continue
        date_m = _DATE_RE.search(card)
        jobs.append(
            NormalizedJob(
                title=title,
                canonical_url=f"https://www.linkedin.com/jobs/view/{job_id}",
                company=company_override or _field(_COMPANY_RE, card),
                location=_field(_LOCATION_RE, card),
                posted_at=date_m.group(1) if date_m else "",
                description="",  # guest cards carry no JD body; quality flags it
                source_adapter=ID,
            )
        )
    return jobs


# JD enrichment (approved-plan #8): the guest cards carry no JD body, but the
# guest per-posting endpoint does — same no-login surface as the search.
_DETAIL_BASE = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"
_VIEW_ID_RE = re.compile(r"/jobs/view/(\d+)")
_DESC_RE = re.compile(
    r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', re.DOTALL
)


def fetch_detail(job: NormalizedJob, fetcher: Fetcher) -> str:
    """The posting's real JD text, from the guest jobPosting endpoint. ""
    when the page has no description block (authwalled/expired) — the caller
    keeps the row with its missing-JD flag rather than failing it."""
    m = _VIEW_ID_RE.search(job.canonical_url)
    if not m:
        return ""
    html = fetcher.get_text(f"{_DETAIL_BASE}/{m.group(1)}", headers=BROWSER_HEADERS)
    d = _DESC_RE.search(html)
    return strip_html(d.group(1)) if d else ""


def search(entry: SourceEntry, prefs: ScanPrefs, fetcher: Fetcher) -> list[NormalizedJob]:
    queries = build_queries(prefs)
    if not queries:
        raise ScraperError(
            ID,
            "LinkedIn search needs at least one role alias — set roles in "
            "onboarding/preferences (a keyword-less search would pull all of LinkedIn)",
        )

    jobs: list[NormalizedJob] = []
    errors: list[str] = []
    for q in queries:
        for page in range(MAX_PAGES):
            url = (
                f"{_BASE}?keywords={quote(q.keyword)}"
                f"&location={quote(q.location)}&start={page * _PAGE_SIZE}"
            )
            try:
                html = fetcher.get_text(url, headers=BROWSER_HEADERS)
            except ScraperError as e:
                # A rate-limit / transient error stops THIS query's pagination
                # (often a 429 after several pages); keep what other queries got.
                errors.append(f"{q.keyword!r}@{q.location!r}: {e}")
                break
            cards = _parse_cards(html, entry.company)
            if not cards:
                break  # empty page → end of this query's results
            jobs.extend(cards)
    if not jobs and errors:
        raise ScraperError(ID, "; ".join(errors))
    return jobs
