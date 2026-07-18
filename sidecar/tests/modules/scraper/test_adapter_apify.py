"""Apify BYO-key actor adapter — fake fetcher only, no live Apify runs.

Covers the discovery-expansion Apify directive (2026-07-18): actor-backed
search sources on the user's own token. Field mappings mirror each actor's
published schema (live-verified 2026-07-18 — see the adapter docstring).
"""

from __future__ import annotations

import pytest

from sidecar.modules.scraper.adapters import apify
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.scraper import scan
from sidecar.modules.scraper.types import ScanPrefs, ScraperError

from .fakes import routed

PREFS = ScanPrefs(
    title_allow=["backend engineer"],
    location_allow=["bangalore"],
    credentials={"apify": "apify_api_TESTTOKEN"},
)

NAUKRI_ITEM = {
    "jobId": "91011",
    "title": "Backend Engineer",
    "staticUrl": "https://www.naukri.com/job-listings-backend-engineer-91011",
    "companyDetail": {"name": "Acme India"},
    "locations": [{"label": "Bengaluru"}, {"label": "Pune"}],
    "description": "<p>Build APIs.</p>",
    "createdDate": "2026-07-10 09:30:00",
}


def _entry(actor: str) -> SourceEntry:
    return SourceEntry(board="apify", actor=actor)


def test_detect_claims_apify_board_rows_with_actor_key():
    from sidecar.modules.scraper import adapters

    resolved = adapters.resolve(_entry("memo23/naukri-scraper"))
    assert resolved is not None
    adapter, key = resolved
    assert adapter.ID == "apify"
    assert key == "apify:memo23/naukri-scraper"
    # Other boards stay unclaimed by apify.
    assert apify.detect(SourceEntry(board="remoteok")) == ""


def test_no_key_raises_clear_settings_hint():
    fetcher = routed({})()
    with pytest.raises(ScraperError, match="no Apify API key"):
        apify.search(
            _entry("memo23/naukri-scraper"),
            ScanPrefs(title_allow=["x"]),
            fetcher,
        )


def test_unsupported_actor_names_the_supported_set():
    fetcher = routed({})()
    with pytest.raises(ScraperError, match="memo23/naukri-scraper"):
        apify.search(_entry("someone/unknown-actor"), PREFS, fetcher)


def test_naukri_run_normalizes_and_sends_token_in_header_only():
    fetcher_cls = routed({"memo23~naukri-scraper/run-sync-get-dataset-items": [NAUKRI_ITEM]})
    fetcher = fetcher_cls()
    jobs = apify.search(_entry("memo23/naukri-scraper"), PREFS, fetcher)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.canonical_url == "https://www.naukri.com/job-listings-backend-engineer-91011"
    assert job.company == "Acme India"
    assert job.location == "Bengaluru, Pune"
    assert job.description == "Build APIs."
    assert job.posted_at == "2026-07-10T09:30:00"
    assert job.source_adapter == "apify"
    # The token travels as a bearer header with the honest UA — never in the
    # URL (fetch errors quote URLs verbatim into persisted diagnostics).
    headers = fetcher.last_post_headers
    assert headers is not None
    assert headers["Authorization"] == "Bearer apify_api_TESTTOKEN"
    assert "findsyoujobs" in headers["User-Agent"]
    assert fetcher.last_post_timeout == apify.RUN_TIMEOUT_S


def test_linkedin_actor_is_one_batched_run_with_guest_canonical_urls():
    captured: list[object] = []

    def _run(url: str, body: object) -> object:
        captured.append(body)
        return [
            {
                "id": "4012345678",
                "title": "Backend Engineer",
                "link": "https://in.linkedin.com/jobs/view/backend-engineer-4012345678?refId=x",
                "companyName": "Acme",
                "location": "Bengaluru, Karnataka, India",
                "descriptionText": "Full JD text.",
                "postedAt": "2026-07-12",
                "salaryInfo": ["₹30L", "₹45L"],
            }
        ]

    fetcher = routed({"curious_coder~linkedin-jobs-scraper": _run})()
    jobs = apify.search(_entry("curious_coder/linkedin-jobs-scraper"), PREFS, fetcher)
    # One batched run: all alias×location pairs in a single actor invocation.
    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, dict)
    assert body["urls"] == [
        "https://www.linkedin.com/jobs/search/?keywords=backend%20engineer&location=bangalore"
    ]
    # Canonicalized to the same /jobs/view/{id} form as the guest adapter →
    # cross-source dedup collapses a job both paths found.
    assert jobs[0].canonical_url == "https://www.linkedin.com/jobs/view/4012345678"
    assert jobs[0].salary == "₹30L – ₹45L"
    assert jobs[0].description == "Full JD text."


def test_seek_builds_url_from_id_and_indeed_drops_relative_dates():
    seek_jobs = apify.search(
        _entry("epicscrapers/seek-job-scraper"),
        PREFS,
        routed(
            {
                "epicscrapers~seek-job-scraper": [
                    {
                        "id": 87654321,
                        "title": "Backend Engineer",
                        "companyName": "Acme AU",
                        "locations": ["Sydney NSW"],
                        "teaser": "Build things.",
                        "listingDate": "2026-07-15T03:00:00Z",
                        "salaryLabel": "$150k",
                    }
                ]
            }
        )(),
    )
    assert seek_jobs[0].canonical_url == "https://www.seek.com.au/job/87654321"
    assert seek_jobs[0].salary == "$150k"

    indeed_jobs = apify.search(
        _entry("misceres/indeed-scraper"),
        PREFS,
        routed(
            {
                "misceres~indeed-scraper": [
                    {
                        "positionName": "Backend Engineer",
                        "url": "https://www.indeed.com/viewjob?jk=abc123",
                        "company": "Acme US",
                        "location": "Remote",
                        "description": "JD body.",
                        "postedAt": "3 days ago",
                        "salary": "$140,000 a year",
                    }
                ]
            }
        )(),
    )
    assert indeed_jobs[0].posted_at == ""  # relative text is not a date
    assert indeed_jobs[0].salary == "$140,000 a year"


def test_partial_run_failure_keeps_other_runs_and_schema_drift_is_loud():
    calls = {"n": 0}

    def _flaky(url: str, body: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ScraperError("fetch", "402 quota exceeded")
        return [NAUKRI_ITEM]

    prefs = ScanPrefs(
        title_allow=["backend engineer", "platform engineer"],
        location_allow=["bangalore"],
        credentials={"apify": "t"},
    )
    jobs = apify.search(
        _entry("memo23/naukri-scraper"), prefs, routed({"memo23~naukri-scraper": _flaky})()
    )
    assert len(jobs) == 1  # second run's rows survive the first run's 402

    # Every run failing with nothing fetched raises verbatim.
    with pytest.raises(ScraperError, match="quota exceeded"):
        apify.search(
            _entry("memo23/naukri-scraper"),
            PREFS,
            routed({"memo23~naukri-scraper": ScraperError("fetch", "402 quota exceeded")})(),
        )

    # Rows that parse to nothing (schema drift) surface as a loud error.
    with pytest.raises(ScraperError, match="schema changed"):
        apify.search(
            _entry("memo23/naukri-scraper"),
            PREFS,
            routed({"memo23~naukri-scraper": [{"totally": "different"}]})(),
        )


def test_scan_integration_runs_apify_beside_other_sources():
    from sidecar.modules.scraper.config import PortalsConfig

    config = PortalsConfig(
        sources=[SourceEntry(board="apify", actor="memo23/naukri-scraper")]
    )
    # Raw module prefs (no app-side synonym expansion): the row's location is
    # "Bengaluru", so allow that spelling here.
    prefs = ScanPrefs(
        title_allow=["backend engineer"],
        location_allow=["bengaluru"],
        credentials={"apify": "apify_api_TESTTOKEN"},
    )
    result = scan(
        config,
        prefs,
        fetcher_factory=routed({"memo23~naukri-scraper": [NAUKRI_ITEM]}),
    )
    report = result.per_source["apify:memo23/naukri-scraper"]
    assert report.fetched == 1
    assert report.kept == 1
    assert result.jobs[0].source_adapter == "apify"


def test_missing_key_is_a_per_source_error_not_a_scan_failure():
    from sidecar.modules.scraper.config import PortalsConfig

    config = PortalsConfig(
        sources=[SourceEntry(board="apify", actor="memo23/naukri-scraper")]
    )
    result = scan(config, ScanPrefs(title_allow=["x"]), fetcher_factory=routed({}))
    report = result.per_source["apify:memo23/naukri-scraper"]
    assert any("no Apify API key" in e for e in report.errors)
    assert result.jobs == []
