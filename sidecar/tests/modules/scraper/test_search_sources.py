"""Covers: the search-source contract (commit 3 of the discovery-expansion
plan) — the `search(entry, prefs, fetcher)` seam, bounded query construction,
the browser-header policy, and the LinkedIn-guest adapter. Fixture HTML only;
zero live network.

Anchors: US-JB-01 (feed breadth via search boards), the §4 fetch-budget
discipline (queries × pages bounded, every call counted in Usage).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidecar.modules.scraper import adapters
from sidecar.modules.scraper.adapters import linkedin_guest
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.http import BROWSER_HEADERS
from sidecar.modules.scraper.scraper import scan
from sidecar.modules.scraper.searchquery import (
    MAX_ALIASES,
    MAX_LOCATIONS,
    MAX_TERMS,
    SearchQuery,
    build_queries,
    select_queries,
)
from sidecar.modules.scraper.types import ScanPrefs, ScraperError

from .fakes import PAYLOADS, FakeFetcher, routed

_LINKEDIN_HTML = (PAYLOADS / "linkedin_guest.html").read_text()

# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def test_build_queries_is_cartesian_and_bounded() -> None:
    prefs = ScanPrefs(
        title_allow=["backend engineer", "platform engineer", "sre", "extra-alias"],
        location_allow=["bengaluru", "remote", "pune", "extra-loc"],
    )
    queries = build_queries(prefs)
    # Capped product, not the full 4×4.
    assert len(queries) == MAX_ALIASES * MAX_LOCATIONS
    assert all(q.keyword and q.location for q in queries)
    assert {q.keyword for q in queries} == {"backend engineer", "platform engineer", "sre"}


def test_build_queries_empty_aliases_yields_nothing() -> None:
    assert build_queries(ScanPrefs(title_allow=[], location_allow=["remote"])) == []


def test_build_queries_no_location_is_location_agnostic() -> None:
    queries = build_queries(ScanPrefs(title_allow=["backend engineer"], location_allow=[]))
    assert len(queries) == 1
    assert queries[0].location == ""


def test_build_queries_user_terms_ride_along_location_less() -> None:
    prefs = ScanPrefs(
        title_allow=["backend engineer"],
        location_allow=["remote"],
        search_terms=["golang site reliability", " ", "rust", "zig", "one-too-many"],
    )
    queries = build_queries(prefs)
    terms = [q for q in queries if q.user_term]
    # Blank entries dropped, then capped.
    assert [q.keyword for q in terms] == ["golang site reliability", "rust", "zig"]
    assert len(terms) == MAX_TERMS
    assert all(q.location == "" for q in terms)


def test_build_queries_terms_alone_suffice() -> None:
    queries = build_queries(ScanPrefs(search_terms=["golang sre"]))
    assert len(queries) == 1
    assert queries[0].user_term


def test_select_queries_terms_never_crowd_out_pairs() -> None:
    pairs = [SearchQuery(keyword=f"p{i}", location="x") for i in range(3)]
    terms = [SearchQuery(keyword=f"t{i}", location="", user_term=True) for i in range(3)]
    picked = select_queries(pairs + terms, 2)
    assert [q.keyword for q in picked] == ["p0", "p1", "t0", "t1"]


# ---------------------------------------------------------------------------
# LinkedIn-guest adapter
# ---------------------------------------------------------------------------


def test_linkedin_detect_claims_board_keyword() -> None:
    assert linkedin_guest.detect(SourceEntry(board="linkedin")) == "linkedin"
    assert linkedin_guest.detect(SourceEntry(board="remoteok")) == ""
    assert linkedin_guest.detect(SourceEntry(url="https://linkedin.com/jobs")) == ""


def test_linkedin_search_parses_cards_and_normalizes_urls() -> None:
    prefs = ScanPrefs(title_allow=["backend engineer"], location_allow=["remote"])
    fetcher = routed({"seeMoreJobPostings/search": "linkedin_guest.html"})()
    jobs = linkedin_guest.search(SourceEntry(board="linkedin"), prefs, fetcher)

    # One query × 2 pages: page 1 returns 2 cards, page 2 (same fixture) returns
    # 2 more — the adapter stops only on an empty page, so both pages fetched.
    assert fetcher.usage.internal_calls == 2
    assert len(jobs) == 4
    first = jobs[0]
    assert first.title == "Backend Engineer"
    # Tracking params stripped → the stable /jobs/view/{id} dedup key.
    assert first.canonical_url == "https://www.linkedin.com/jobs/view/3901234567"
    assert first.company == "Acme Corp"
    assert first.location == "Bengaluru, Karnataka, India"
    assert first.posted_at == "2026-07-15"
    assert first.source_adapter == "linkedin"


def test_linkedin_search_stops_on_empty_page() -> None:
    prefs = ScanPrefs(title_allow=["backend engineer"], location_allow=["remote"])
    # First page has cards, second page is empty → pagination stops at 2 calls.
    # A callable returns literal content (see FakeFetcher), so read the fixture.
    pages = iter([_LINKEDIN_HTML, "<ul></ul>"])

    def respond(_url: str, _body: object) -> str:
        return next(pages)

    fetcher = routed({"seeMoreJobPostings/search": respond})()
    jobs = linkedin_guest.search(SourceEntry(board="linkedin"), prefs, fetcher)
    assert fetcher.usage.internal_calls == 2
    assert len(jobs) == 2  # only the non-empty page contributed


def test_linkedin_search_requires_role_alias() -> None:
    fetcher = routed({"seeMoreJobPostings/search": "linkedin_guest.html"})()
    with pytest.raises(ScraperError, match="role alias"):
        linkedin_guest.search(SourceEntry(board="linkedin"), ScanPrefs(), fetcher)


def test_linkedin_rate_limit_keeps_partial_results() -> None:
    prefs = ScanPrefs(
        title_allow=["backend engineer", "sre"], location_allow=["remote"]
    )
    calls = {"n": 0}

    def respond(_url: str, _body: object) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return _LINKEDIN_HTML  # first query, first page: 2 cards
        raise ScraperError("fetch", "could not fetch ...: HTTP Error 429: Too Many Requests")

    fetcher = routed({"seeMoreJobPostings/search": respond})()
    jobs = linkedin_guest.search(SourceEntry(board="linkedin"), prefs, fetcher)
    # The 429 stops pagination but the first page's rows survive (rank-don't-gate).
    assert len(jobs) == 2


def test_linkedin_all_queries_fail_raises() -> None:
    prefs = ScanPrefs(title_allow=["backend engineer"], location_allow=["remote"])
    fetcher = routed(
        {"seeMoreJobPostings/search": ScraperError("fetch", "HTTP Error 429")}
    )()
    with pytest.raises(ScraperError, match="429"):
        linkedin_guest.search(SourceEntry(board="linkedin"), prefs, fetcher)


# ---------------------------------------------------------------------------
# Header policy + the scan() search seam
# ---------------------------------------------------------------------------


def test_browser_headers_have_no_findsyoujobs_ua() -> None:
    """Search adapters send a browser UA (the honest bot UA is refused); the
    policy line stays browser-headers-only (no proxy/TLS forgery)."""
    assert "findsyoujobs" not in BROWSER_HEADERS["User-Agent"].lower()
    assert BROWSER_HEADERS["User-Agent"].startswith("Mozilla/")


class HeaderSpyFetcher(FakeFetcher):
    """Records the headers each get_text call receives."""

    seen_headers: list[dict[str, str] | None] = []

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        type(self).seen_headers.append(headers)
        return super().get_text(url, headers=headers)


def test_linkedin_sends_browser_headers() -> None:
    prefs = ScanPrefs(title_allow=["backend engineer"], location_allow=["remote"])
    spy = type(
        "Spy",
        (HeaderSpyFetcher,),
        {"routes": {"search": "linkedin_guest.html"}, "seen_headers": []},
    )
    fetcher = spy()
    linkedin_guest.search(SourceEntry(board="linkedin"), prefs, fetcher)
    assert fetcher.seen_headers
    assert all(h == BROWSER_HEADERS for h in fetcher.seen_headers)


def test_guest_search_is_sessionless_and_isolated_from_voyager() -> None:
    """The guest adapter can never ride the user's logged-in LinkedIn session:
    the fetcher is stateless (no cookie jar), BROWSER_HEADERS carries no
    Cookie/Authorization, and nothing under modules/scraper/ references
    voyager or cookie-jar machinery. Logged-in LinkedIn lives only in the
    networking module behind its default-off toggle."""
    assert not {"cookie", "authorization"} & {k.lower() for k in BROWSER_HEADERS}

    import sidecar.modules.scraper as scraper_pkg

    scraper_root = Path(scraper_pkg.__file__).parent
    banned = ("voyager", "CookieJar", "cookiejar", "cookielib")
    for py in scraper_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{py.relative_to(scraper_root)} references {token!r}"


def test_scan_routes_search_adapters_through_search_seam() -> None:
    """scan() calls search() (not fetch) for a search source and runs the same
    downstream filter chain — the query narrows, the local filter refines."""
    from sidecar.modules.scraper.config import PortalsConfig

    config = PortalsConfig(
        sources=[SourceEntry(board="linkedin")],
        prefs=ScanPrefs(title_allow=["backend engineer"], location_allow=["remote"]),
    )
    detail_html = (
        '<div class="show-more-less-html__markup relative">'
        "<p>Design and run backend services.</p></div>"
    )
    result = scan(
        config,
        prefs=ScanPrefs(
            title_allow=["backend engineer"],
            location_allow=["remote", "india"],
        ),
        fetcher_factory=routed(
            {
                "seeMoreJobPostings/search": "linkedin_guest.html",
                # JD enrichment (approved-plan #8): kept JD-less rows fetch
                # the guest per-posting detail in the same scan.
                "jobs-guest/jobs/api/jobPosting/": lambda u, b: detail_html,
            }
        ),
    )
    key = "linkedin:linkedin"
    assert key in result.per_source
    assert not result.per_source[key].errors
    # Both fixture rows match the title/location filters and survive dedup.
    assert len(result.jobs) == 2
    assert all(j.source_adapter == "linkedin" for j in result.jobs)
    # …and both were enriched with the real JD text.
    assert all(j.description == "Design and run backend services." for j in result.jobs)


def test_registry_resolves_linkedin_board() -> None:
    resolved = adapters.resolve(SourceEntry(board="linkedin"))
    assert resolved is not None
    _adapter, key = resolved
    assert key == "linkedin:linkedin"
