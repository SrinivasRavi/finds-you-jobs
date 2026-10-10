"""Add-by-URL probe tests — probe_url resolves one pasted URL to a job.

Covers:
  US-JB-07 — Add-by-URL escape hatch (best-effort title/company/location/desc)
  Track M3 / JD-gap — a known ATS job URL comes back fully structured (with the
  JD body the content-in-list params now carry).
"""

from __future__ import annotations

import pytest

from sidecar.modules.scraper.probe import probe_url
from sidecar.modules.scraper.types import ScraperError

from .fakes import routed

# FakeFetcher serves a str payload from the payloads/ dir → point at the fixture.
_GENERIC_PAGE = "generic_careers.html"


def test_probe_matches_greenhouse_job_url_with_description() -> None:
    # A specific Greenhouse *job* URL → the board is fetched, the matching row
    # returned, carrying the JD body from ?content=true.
    fetcher = routed({"boards-api.greenhouse.io/v1/boards/gleanwork/jobs": "greenhouse.json"})
    job = probe_url(
        "https://job-boards.greenhouse.io/gleanwork/jobs/4661886005",
        fetcher_factory=fetcher,
    )
    assert job.source_adapter == "greenhouse"
    assert job.title
    assert job.canonical_url.endswith("/gleanwork/jobs/4661886005")
    assert job.description  # JD body landed from the list request
    assert "<" not in job.description


def test_probe_generic_page_extracts_title_and_body() -> None:
    fetcher = routed({"acme.example.com": _GENERIC_PAGE})
    job = probe_url("https://acme.example.com/careers/staff-platform", fetcher_factory=fetcher)
    assert job.source_adapter == "paste-url"
    assert job.title == "Staff Platform Engineer"  # <h1> preferred over <title>
    assert "build systems" in job.description
    assert "<" not in job.description


def test_probe_board_url_with_no_matching_job_falls_back_to_generic() -> None:
    # A Greenhouse board *root* URL isn't a specific job → no row matches the
    # canonical, so we still return a best-effort generic draft (never crash).
    fetcher = routed(
        {
            "boards-api.greenhouse.io/v1/boards/gleanwork/jobs": "greenhouse.json",
            "job-boards.greenhouse.io/gleanwork": _GENERIC_PAGE,
        }
    )
    job = probe_url("https://job-boards.greenhouse.io/gleanwork", fetcher_factory=fetcher)
    assert job.source_adapter == "paste-url"
    assert job.title  # generic extraction produced something


def test_probe_rejects_non_http_url() -> None:
    with pytest.raises(ScraperError) as ei:
        probe_url("not-a-real-url")
    assert "[probe]" in str(ei.value)


def test_probe_lever_job_url_takes_the_single_posting_endpoint() -> None:
    # One pasted Lever job URL asks for that job, never the board. The board
    # list is 20 MB on a big tenant and the fetcher refuses it outright, which
    # is what the maintainer hit on 2026-10-08.
    posting = {
        "text": "Apple Watch User Research",
        "hostedUrl": "https://jobs.lever.co/tsmg/c60ccb3a",
        "categories": {"location": "All states"},
        "descriptionPlain": "Participate in a research project.",
        "createdAt": 1759000000000,
    }
    fetcher = routed({"api.lever.co/v0/postings/tsmg/c60ccb3a": posting})
    job = probe_url("https://jobs.lever.co/tsmg/c60ccb3a", fetcher_factory=fetcher)
    assert job.source_adapter == "lever"
    assert job.title == "Apple Watch User Research"
    assert job.description
    assert job.location == "All states"


def test_probe_falls_back_to_generic_when_the_board_read_is_refused() -> None:
    # The Lever board list over the fetcher's 20 MB cap used to surface as a
    # 422 in the Add-a-job modal. The user gets a generic draft instead.
    too_big = ScraperError("fetch", "returned more than 20971520 bytes; refusing")
    fetcher = routed(
        {
            "api.lever.co/v0/postings/tsmg": too_big,
            "jobs.lever.co/tsmg": _GENERIC_PAGE,
        }
    )
    job = probe_url("https://jobs.lever.co/tsmg", fetcher_factory=fetcher)
    assert job.source_adapter == "paste-url"
    assert job.title
