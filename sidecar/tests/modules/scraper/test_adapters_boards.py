"""Board-feed adapter tests — canned real payloads, no network.

Covers:
  US-JB-10 — source-adapter attribution on every row
  Track M3 spec — per-source adapters over public board feeds
    (remoteok, remotive, hackernews, rss/atom)
"""

from __future__ import annotations

import pytest

from sidecar.modules.scraper.adapters import hackernews, remoteok, remotive, rss
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.http import Fetcher
from sidecar.modules.scraper.types import ScraperError, Usage

from .fakes import routed


class _RawTextFetcher(Fetcher):
    """Serves one raw text body — for hand-built XML the file-based routes can't carry."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.usage = Usage()

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        self.usage.internal_calls += 1
        return self.text

# --------------------------------------------------------------------------- #
# remoteok
# --------------------------------------------------------------------------- #


def test_remoteok_detect_claims_board_and_host():
    assert remoteok.detect(SourceEntry(board="remoteok")) == "remoteok.com"
    assert remoteok.detect(SourceEntry(url="https://remoteok.com/api")) == "remoteok.com"
    assert remoteok.detect(SourceEntry(url="https://www.remoteok.com/")) == "remoteok.com"
    assert remoteok.detect(SourceEntry(url="https://remoteok.io/remote-jobs")) == "remoteok.com"
    assert remoteok.detect(SourceEntry(url="https://remotive.com/api/remote-jobs")) == ""
    assert remoteok.detect(SourceEntry(board="remoteok", type="rss")) == ""


def test_remoteok_fetch_normalizes_and_skips_legal_notice():
    fetcher = routed({"remoteok.com/api": "remoteok.json"})()
    jobs = remoteok.fetch(SourceEntry(board="remoteok"), fetcher)
    # Fixture has 4 elements: one legal notice (no position/url) + 3 postings.
    assert len(jobs) == 3
    assert all(j.source_adapter == "remoteok" for j in jobs)
    first = jobs[0]
    assert first.title == "Business Analyst"
    assert first.canonical_url.startswith("https://remoteOK.com/remote-jobs/")
    assert first.posted_at == "2026-07-05T17:18:41+00:00"
    assert first.company == "Rotaract Club of NIBM Kandy"
    assert "About the Role" in first.description  # HTML stripped
    assert fetcher.usage.internal_calls == 1  # one list request


def test_remoteok_salary_formatting():
    def row(letter: str, smin: int, smax: int) -> dict:
        return {
            "position": letter,
            "url": f"https://remoteok.com/{letter}",
            "salary_min": smin,
            "salary_max": smax,
        }

    payload = [
        row("A", 50000, 80000),
        row("B", 90000, 0),
        row("C", 0, 0),
        row("D", 70000, 70000),
    ]
    fetcher = routed({"remoteok.com/api": payload})()
    jobs = remoteok.fetch(SourceEntry(board="remoteok"), fetcher)
    assert jobs[0].salary == "$50,000–$80,000"
    assert jobs[1].salary == "$90,000+"  # max unknown
    assert jobs[2].salary == ""  # both unknown
    assert jobs[3].salary == "$70,000+"  # max not greater than min


def test_remoteok_bad_payload_is_typed_error():
    fetcher = routed({"remoteok.com/api": {"nope": True}})()
    with pytest.raises(ScraperError) as ei:
        remoteok.fetch(SourceEntry(board="remoteok"), fetcher)
    assert "[remoteok]" in str(ei.value)


# --------------------------------------------------------------------------- #
# remotive
# --------------------------------------------------------------------------- #


def test_remotive_detect_claims_board_and_host():
    assert remotive.detect(SourceEntry(board="remotive")) == "remotive.com"
    api = "https://remotive.com/api/remote-jobs"
    assert remotive.detect(SourceEntry(url=api)) == "remotive.com"
    assert remotive.detect(SourceEntry(url="https://remotive.io/")) == "remotive.com"
    assert remotive.detect(SourceEntry(url="https://remoteok.com/api")) == ""
    assert remotive.detect(SourceEntry(board="remotive", type="greenhouse")) == ""


def test_remotive_fetch_normalizes_real_payload():
    fetcher = routed({"remotive.com/api/remote-jobs": "remotive.json"})()
    jobs = remotive.fetch(SourceEntry(board="remotive"), fetcher)
    assert jobs and all(j.source_adapter == "remotive" for j in jobs)
    first = jobs[0]
    assert first.title == "Freelance Writer"
    assert first.canonical_url == "https://remotive.com/remote-jobs/writing/freelance-writer-1185979"
    assert first.company == "IAPWE"
    assert first.location == "Worldwide"  # candidate_required_location mapped
    assert first.posted_at == "2026-07-04T16:53:04"
    assert first.salary == "$50-$75 /hour"
    assert "content writers" in first.description  # HTML stripped
    assert fetcher.usage.internal_calls == 1


def test_remotive_bad_payload_is_typed_error():
    fetcher = routed({"remotive.com/api/remote-jobs": [1, 2, 3]})()
    with pytest.raises(ScraperError) as ei:
        remotive.fetch(SourceEntry(board="remotive"), fetcher)
    assert "[remotive]" in str(ei.value)


# --------------------------------------------------------------------------- #
# hackernews
# --------------------------------------------------------------------------- #


def test_hackernews_detect_claims_board_and_host():
    assert hackernews.detect(SourceEntry(board="hackernews")) == "whoishiring"
    assert hackernews.detect(SourceEntry(board="hn")) == "whoishiring"
    assert hackernews.detect(
        SourceEntry(url="https://news.ycombinator.com/item?id=48747976")
    ) == "whoishiring"
    assert hackernews.detect(SourceEntry(url="https://remoteok.com/api")) == ""
    assert hackernews.detect(SourceEntry(board="hn", type="rss")) == ""


_HN_ROUTES = {
    "author_whoishiring": "hn_story.json",
    "tags=comment,story_": "hn_comments.json",
}


def test_hackernews_discovery_path_two_calls_and_pipe_parse():
    fetcher = routed(_HN_ROUTES)()
    jobs = hackernews.fetch(SourceEntry(board="hn"), fetcher)
    assert fetcher.usage.internal_calls == 2  # story discovery + comments
    assert all(j.source_adapter == "hackernews" for j in jobs)
    # Fixture: 3 top-level pipe comments + 1 non-top-level (parent 48772440) skipped.
    assert len(jobs) == 3
    first = jobs[0]
    assert first.company == "Sphinx Defense"
    assert first.title == "Software Engineering"
    assert first.location == "Remote (US Only)"
    assert first.canonical_url == "https://news.ycombinator.com/item?id=48807202"
    assert first.posted_at == "2026-07-06T16:46:08Z"
    # The reply nested under a different parent must not appear.
    assert all("48808119" not in j.canonical_url for j in jobs)


def test_hackernews_pinned_url_skips_discovery():
    fetcher = routed(_HN_ROUTES)()
    jobs = hackernews.fetch(
        SourceEntry(url="https://news.ycombinator.com/item?id=48747976"), fetcher
    )
    assert fetcher.usage.internal_calls == 1  # comments only; story id was pinned
    assert len(jobs) == 3


def test_hackernews_non_pipe_falls_back_to_truncated_first_line():
    long_line = "x" * 200
    payload = {
        "hits": [
            {
                "objectID": "1",
                "parent_id": 48747976,
                "comment_text": long_line + "<p>more body</p>",
                "created_at": "2026-07-06T00:00:00Z",
            }
        ]
    }
    routes = {"author_whoishiring": "hn_story.json", "tags=comment,story_": payload}
    fetcher = routed(routes)()
    jobs = hackernews.fetch(SourceEntry(board="hn"), fetcher)
    assert len(jobs) == 1
    assert jobs[0].company == ""
    assert jobs[0].title == "x" * 120  # truncated to 120 chars
    assert jobs[0].location == ""


def test_hackernews_no_story_is_typed_error():
    hits = {"hits": [{"objectID": "9", "title": "Ask HN: Something else"}]}
    fetcher = routed({"author_whoishiring": hits})()
    with pytest.raises(ScraperError) as ei:
        hackernews.fetch(SourceEntry(board="hn"), fetcher)
    assert "[hackernews]" in str(ei.value)


# --------------------------------------------------------------------------- #
# rss / atom
# --------------------------------------------------------------------------- #


def test_rss_detect_claims_type_and_feed_paths():
    wwr = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    assert rss.detect(SourceEntry(url=wwr)) == "weworkremotely.com"
    assert rss.detect(SourceEntry(url="https://example.com/jobs.xml")) == "example.com"
    assert rss.detect(SourceEntry(url="https://example.com/feed")) == "example.com"
    assert rss.detect(SourceEntry(url="https://example.com/atom", type="rss")) == "example.com"
    assert rss.detect(SourceEntry(url="https://boards.greenhouse.io/gleanwork")) == ""
    assert rss.detect(SourceEntry(board="remoteok")) == ""  # no url
    assert rss.detect(SourceEntry(url=wwr, type="greenhouse")) == ""


def test_rss_fetch_wwr_region_and_title_split():
    wwr = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    fetcher = routed({"weworkremotely.com": "wwr.rss"})()
    jobs = rss.fetch(SourceEntry(url=wwr, type="rss"), fetcher)
    assert jobs and all(j.source_adapter == "rss" for j in jobs)
    first = jobs[0]
    assert first.company == "Drivetrain"  # "Company: Role" split
    assert first.title == "UI/UX Website Designer (copy)"
    assert first.location == "Anywhere in the World"  # <region> mapped
    assert first.canonical_url.startswith("https://weworkremotely.com/remote-jobs/")
    assert first.posted_at == "2026-06-15T20:07:47+00:00"  # RFC 822 -> ISO
    assert first.description  # HTML stripped, non-empty


def test_rss_fetch_atom_feed():
    atom = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Example Jobs</title>"
        "<entry>"
        "<title>Acme: Backend Engineer</title>"
        '<link rel="self" href="https://example.com/self"/>'
        '<link rel="alternate" href="https://example.com/jobs/1"/>'
        "<published>2026-07-01T12:00:00Z</published>"
        "<summary>&lt;p&gt;Build things.&lt;/p&gt;</summary>"
        "</entry>"
        "</feed>"
    )
    fetcher = _RawTextFetcher(atom)
    jobs = rss.fetch(SourceEntry(url="https://example.com/atom", type="rss"), fetcher)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source_adapter == "rss"
    assert job.company == "Acme"
    assert job.title == "Backend Engineer"
    assert job.canonical_url == "https://example.com/jobs/1"  # rel=alternate preferred
    assert job.posted_at == "2026-07-01T12:00:00Z"
    assert job.description == "Build things."


def test_rss_unparseable_xml_is_typed_error():
    fetcher = _RawTextFetcher("<feed><entry>oops")
    with pytest.raises(ScraperError) as ei:
        rss.fetch(SourceEntry(url="https://example.com/atom", type="rss"), fetcher)
    assert "[rss]" in str(ei.value)
