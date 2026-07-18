"""Brave Search meta-discovery adapter — fake fetcher only, no live queries.

Covers approved-plan commit 5: site:-scoped ATS queries from the user's own
Brave key, results → lean candidate rows into the shared funnel.
"""

from __future__ import annotations

import pytest

from sidecar.modules.scraper.adapters import brave
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.types import ScanPrefs, ScraperError

from .fakes import FakeFetcher, routed

PREFS = ScanPrefs(
    title_allow=["backend engineer"],
    location_allow=["bangalore"],
    credentials={"brave": "BSA-test-key"},
)

_RESULT = {
    "web": {
        "results": [
            {
                "title": "Job Application for Backend Engineer at Acme",
                "url": "https://boards.greenhouse.io/acme/jobs/1234?gh_src=abc",
                "description": "Build <strong>APIs</strong> in Bangalore.",
            },
            {
                "title": "Some blogspam about jobs",
                "url": "https://blog.example.com/jobs",
                "description": "not an ATS result",
            },
        ]
    }
}


def _entry() -> SourceEntry:
    return SourceEntry(board="brave")


def test_detect_claims_brave_board_only():
    from sidecar.modules.scraper import adapters

    resolved = adapters.resolve(_entry())
    assert resolved is not None and resolved[1] == "brave:search"
    assert brave.detect(SourceEntry(board="remoteok")) == ""


def test_no_key_raises_clear_settings_hint():
    with pytest.raises(ScraperError, match="no Brave Search API key"):
        brave.search(_entry(), ScanPrefs(title_allow=["x"]), routed({})())


def test_search_scopes_queries_filters_offsite_and_parses_gh_title():
    class Capture(FakeFetcher):
        routes = {"api.search.brave.com": _RESULT}
        urls: list[str] = []
        headers_seen: list[dict] = []

        def get_json(self, url, headers=None):  # noqa: ANN001, ANN201
            Capture.urls.append(url)
            Capture.headers_seen.append(dict(headers or {}))
            return super().get_json(url, headers=headers)

    jobs = brave.search(_entry(), PREFS, Capture())
    # One query per ATS site × bounded pairs; freshness=pw on each; the key in
    # the X-Subscription-Token header, never the URL.
    assert len(Capture.urls) == len(brave.ATS_SITES)  # 1 alias×loc pair here
    assert all("freshness=pw" in u for u in Capture.urls)
    assert all("BSA-test-key" not in u for u in Capture.urls)
    assert all(h.get("X-Subscription-Token") == "BSA-test-key" for h in Capture.headers_seen)

    # Off-ATS results are dropped; each ATS site contributed the same fixture
    # row for the greenhouse hosts, so canonical dedup happens downstream.
    assert all("greenhouse.io" in j.canonical_url for j in jobs)
    job = jobs[0]
    assert job.title == "Backend Engineer"
    assert job.company == "Acme"
    assert job.description == "Build APIs in Bangalore."
    assert job.source_adapter == "brave"


def test_partial_query_failures_keep_other_results():
    calls = {"n": 0}

    # Fail every site EXCEPT boards.greenhouse.io (the one whose fixture row
    # matches) — its results must survive the other queries' 429s.
    def _flaky(url: str, body: object) -> object:
        calls["n"] += 1
        if "site%3Aboards.greenhouse.io" not in url:
            raise ScraperError("fetch", "429 rate limited")
        return _RESULT

    jobs = brave.search(_entry(), PREFS, routed({"api.search.brave.com": _flaky})())
    assert jobs  # later sites' results survive the first 429

    with pytest.raises(ScraperError, match="rate limited"):
        brave.search(
            _entry(),
            PREFS,
            routed({"api.search.brave.com": ScraperError("fetch", "429 rate limited")})(),
        )
