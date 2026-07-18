"""JD enrichment (approved-plan #8) — fetch_detail fills missing JDs in-scan.

JD available → nothing happens. JD missing → the adapter's `fetch_detail`
pulls the real JD (LinkedIn guest posting endpoint, Workday CxS detail) so
the job scores normally. Enrichment failure/impossibility keeps the row —
the lenient alias+location match already admitted it.
"""

from __future__ import annotations

from sidecar.modules.scraper.adapters import linkedin_guest, workday
from sidecar.modules.scraper.config import PortalsConfig, SourceEntry
from sidecar.modules.scraper.scraper import ENRICH_CAP, scan
from sidecar.modules.scraper.types import NormalizedJob, ScanPrefs, ScraperError

from .fakes import routed

_DETAIL_HTML = (
    '<div class="show-more-less-html__markup">'
    "<p>Own the <b>backend</b> platform.</p></div>"
)


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


def test_scan_enrichment_failure_keeps_row_and_records_error():
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
    assert result.jobs, "rows survive a failed enrichment"
    assert all(j.description == "" for j in result.jobs)
    assert any("enrich" in e and "429 slow down" in e for e in report.errors)


def test_enrich_cap_bounds_detail_fetches():
    calls = {"n": 0}

    def _detail(url: str, body: object) -> str:
        calls["n"] += 1
        return _DETAIL_HTML

    def _cards(url: str, body: object) -> str:
        if "start=0" not in url:
            return "<ul></ul>"
        rows = "".join(
            f'<li ><div class="base-card" data-entity-urn="urn:li:jobPosting:{i}">'
            f'<h3 class="base-search-card__title">Backend Engineer {i}</h3></div></li>'
            for i in range(ENRICH_CAP + 10)
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
    assert len(result.jobs) == ENRICH_CAP + 10
    assert calls["n"] == ENRICH_CAP
    enriched = [j for j in result.jobs if j.description]
    assert len(enriched) == ENRICH_CAP
