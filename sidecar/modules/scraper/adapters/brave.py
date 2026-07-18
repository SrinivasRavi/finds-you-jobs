"""Brave Search meta-discovery — BYO-key candidate-URL search source.

Approved-plan commit 5 (2026-07-17): one Brave Web Search query per
(ATS-domain × role-alias × location), `freshness=pw` (past week), the user's
own Brave Search API key (free tier ≈ 2,000 queries/mo — the app keeps a
monthly ledger and stops querying at the cap). Results are job-posting URLs
on ATS domains we already parse; each becomes a lean candidate row that rides
the normal funnel — title/location filters refine it, canonical dedup
collapses it with rows the first-party adapters found, and the JD arrives via
enrichment. Complement, not substitute: this catches boards *not in the
user's registry* (a company on Greenhouse we never listed).

The key travels in the `X-Subscription-Token` header (Brave's scheme), never
in the URL. Query volume per scan is bounded: `MAX_PAIRS` alias×location
pairs × the ATS domain list.
"""

from __future__ import annotations

from urllib.parse import quote

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import USER_AGENT, Fetcher
from ..searchquery import build_queries
from ..types import NormalizedJob, ScanPrefs, ScraperError

ID = "brave"
_BASE = "https://api.search.brave.com/res/v1/web/search"

# ATS hosts whose public job URLs we can parse/apply against. Deliberately the
# hosted-URL ATSes only — self-hosted patterns (Workday tenant domains) can't
# be site:-scoped in one query.
ATS_SITES: tuple[str, ...] = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "jobs.smartrecruiters.com",
)

MAX_PAIRS = 2  # alias×location pairs per scan → ≤ MAX_PAIRS × len(ATS_SITES) queries
_COUNT = 20


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    return "search" if entry.board == ID else ""


def _clean_title(raw: str) -> tuple[str, str]:
    """(title, company) from a search-result title. Handles the one dominant
    pattern honestly (Greenhouse's "Job Application for X at Y"); anything
    else stays verbatim with company unknown."""
    title = strip_html(raw)
    if title.startswith("Job Application for "):
        rest = title[len("Job Application for ") :]
        if " at " in rest:
            role, company = rest.rsplit(" at ", 1)
            return role.strip(), company.strip()
        return rest.strip(), ""
    return title, ""


def search(entry: SourceEntry, prefs: ScanPrefs, fetcher: Fetcher) -> list[NormalizedJob]:
    key = prefs.credentials.get("brave", "")
    if not key:
        raise ScraperError(
            ID,
            "no Brave Search API key — add yours in Settings → Discovery sources "
            "(free tier: ~2,000 queries/month), or untick this source",
        )
    queries = build_queries(prefs)
    if not queries:
        raise ScraperError(
            ID,
            "Brave search needs at least one role alias — set roles in "
            "onboarding/preferences",
        )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "X-Subscription-Token": key,
    }
    jobs: list[NormalizedJob] = []
    errors: list[str] = []
    for q in queries[:MAX_PAIRS]:
        for site in ATS_SITES:
            term = f"site:{site} {q.keyword}"
            if q.location:
                term += f" {q.location}"
            url = f"{_BASE}?q={quote(term)}&freshness=pw&count={_COUNT}"
            try:
                data = fetcher.get_json(url, headers=headers)
            except ScraperError as e:
                # A 429/401 on one query never kills the others; verbatim into
                # per-source diagnostics.
                errors.append(f"{term!r}: {e}")
                continue
            results = (
                data.get("web", {}).get("results", []) if isinstance(data, dict) else []
            )
            if not isinstance(results, list):
                continue
            for r in results:
                if not isinstance(r, dict):
                    continue
                result_url = str(r.get("url", ""))
                raw_title = str(r.get("title", ""))
                # Only rows actually on the asked-for ATS host — search engines
                # sometimes pad with related results.
                if site not in result_url or not raw_title:
                    continue
                title, company = _clean_title(raw_title)
                jobs.append(
                    NormalizedJob(
                        title=title,
                        canonical_url=result_url,
                        company=company or entry.company,
                        description=strip_html(str(r.get("description", ""))),
                        source_adapter=ID,
                    )
                )
    if not jobs and errors:
        raise ScraperError(ID, "; ".join(errors))
    return jobs
