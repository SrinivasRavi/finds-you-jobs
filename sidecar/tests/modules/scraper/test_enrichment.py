"""JD enrichment (approved-plan #8) — fetch_detail fills missing JDs in-scan.

JD available → nothing happens. JD missing → the adapter's `fetch_detail`
pulls the real JD (LinkedIn guest posting endpoint, Workday CxS detail,
BambooHR per-job detail) so the job scores normally. A JD still missing (or
still too thin to score) after enrichment gets its row dropped, not kept as
a husk (item 7) — the failed/impossible enrichment's error is still recorded.
"""

from __future__ import annotations

from sidecar.modules.scraper.adapters import linkedin_guest, workday
from sidecar.modules.scraper.config import PortalsConfig, SourceEntry
from sidecar.modules.scraper.scraper import SCAN_ENRICH_CAP, scan
from sidecar.modules.scraper.types import NormalizedJob, ScanPrefs, ScraperError

from .fakes import routed

_DETAIL_HTML = (
    '<div class="show-more-less-html__markup">'
    "<p>Own the <b>backend</b> platform.</p></div>"
)

# Long enough to clear scan()'s post-enrich MIN_JD_CHARS drop.
_LONG_JD = (
    "We build reliable systems for our users every day, working closely "
    "with product and design across the whole stack from planning to "
    "on-call, end to end, every single week without exception, rain or shine."
)
_LONG_JD_HTML = f'<div class="show-more-less-html__markup"><p>{_LONG_JD}</p></div>'


def test_linkedin_fetch_detail_parses_guest_posting():
    job = NormalizedJob(
        title="Backend Engineer",
        canonical_url="https://www.linkedin.com/jobs/view/4012345678",
    )
    fetcher = routed({"jobs-guest/jobs/api/jobPosting/4012345678": lambda u, b: _DETAIL_HTML})()
    assert linkedin_guest.fetch_detail(job, fetcher) == "Own the backend platform."
    # No numeric id in the URL → honestly nothing to fetch.
    other = NormalizedJob(title="X", canonical_url="https://example.com/job")
    assert linkedin_guest.fetch_detail(other, routed({})()) == ""


def test_workday_fetch_detail_reads_cxs_job_posting_info():
    job = NormalizedJob(
        title="Backend Engineer",
        canonical_url=(
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
            "/job/US-CA-Santa-Clara/Backend-Engineer_JR123"
        ),
    )
    fetcher = routed(
        {
            "/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/": {
                "jobPostingInfo": {"jobDescription": "<p>Build GPU tooling.</p>"}
            }
        }
    )()
    assert workday.fetch_detail(job, fetcher) == "Build GPU tooling."
    # Unexpected shape degrades to "" (row keeps its missing-JD flag).
    assert (
        workday.fetch_detail(
            job, routed({"/wday/cxs/nvidia/": {"unexpected": True}})()
        )
        == ""
    )


def test_scan_enrichment_failure_drops_row_but_records_error_and_drop():
    config = PortalsConfig(sources=[SourceEntry(board="linkedin")])
    result = scan(
        config,
        ScanPrefs(title_allow=["backend engineer"]),
        fetcher_factory=routed(
            {
                "seeMoreJobPostings/search": "linkedin_guest.html",
                "jobs-guest/jobs/api/jobPosting/": ScraperError("fetch", "429 slow down"),
            }
        ),
    )
    report = result.per_source["linkedin:linkedin"]
    assert result.jobs == []  # no row got a scorable description
    assert report.dropped_no_description  # every JD-less row is logged, not vanished
    assert any("enrich" in e and "429 slow down" in e for e in report.errors)


def test_scan_enrichment_contains_unexpected_exception():
    """F-H6 — a non-ScraperError out of fetch_detail is contained per row,
    keeping the rest of the scan running; the row itself still gets dropped
    for lacking a scorable description."""
    config = PortalsConfig(sources=[SourceEntry(board="linkedin")])
    result = scan(
        config,
        ScanPrefs(title_allow=["backend engineer"]),
        fetcher_factory=routed(
            {
                "seeMoreJobPostings/search": "linkedin_guest.html",
                "jobs-guest/jobs/api/jobPosting/": TypeError("adapter bug"),
            }
        ),
    )
    report = result.per_source["linkedin:linkedin"]
    assert result.jobs == []
    assert report.dropped_no_description
    assert any("unexpected TypeError: adapter bug" in e for e in report.errors)


def test_search_enrich_cap_is_unbounded_for_linkedin():
    """SEARCH_ENRICH_CAP is unbounded — a search-shaped source already bounds
    its own row count at query time, so every kept row gets enriched."""
    calls = {"n": 0}

    def _detail(url: str, body: object) -> str:
        calls["n"] += 1
        return _LONG_JD_HTML

    row_count = 30  # comfortably above the old shared ENRICH_CAP of 20

    def _cards(url: str, body: object) -> str:
        if "start=0" not in url:
            return "<ul></ul>"
        rows = "".join(
            f'<li ><div class="base-card" data-entity-urn="urn:li:jobPosting:{i}">'
            f'<h3 class="base-search-card__title">Backend Engineer {i}</h3></div></li>'
            for i in range(row_count)
        )
        return f"<ul>{rows}</ul>"

    result = scan(
        PortalsConfig(sources=[SourceEntry(board="linkedin")]),
        ScanPrefs(title_allow=["backend engineer"]),
        fetcher_factory=routed(
            {
                "seeMoreJobPostings/search": _cards,
                "jobs-guest/jobs/api/jobPosting/": _detail,
            }
        ),
    )
    assert calls["n"] == row_count
    assert len(result.jobs) == row_count
    assert all(j.description for j in result.jobs)


def test_scan_enrich_cap_bounds_enumerate_shaped_source():
    """SCAN_ENRICH_CAP bounds enrichment for enumerate-shaped (ATS/board)
    sources — unlike search-shaped LinkedIn, a company feed can run into the
    thousands. Rows past the cap keep no JD, so the drop phase removes them."""
    calls = {"n": 0}

    def _detail(url: str, body: object) -> dict:
        calls["n"] += 1
        return {"jobOpening": {"description": _LONG_JD_HTML}}

    row_count = SCAN_ENRICH_CAP + 10
    listing = {
        "result": [
            {
                "id": str(i),
                "jobOpeningName": f"Engineer {i}",
                "location": {"city": "Pune", "state": "MH"},
            }
            for i in range(row_count)
        ]
    }
    result = scan(
        PortalsConfig(sources=[SourceEntry(url="https://acme.bamboohr.com/careers")]),
        ScanPrefs(),
        fetcher_factory=routed(
            {"acme.bamboohr.com/careers/list": listing, "/detail": _detail}
        ),
    )
    assert calls["n"] == SCAN_ENRICH_CAP
    assert len(result.jobs) == SCAN_ENRICH_CAP
    assert "bamboohr:acme" in result.per_source
