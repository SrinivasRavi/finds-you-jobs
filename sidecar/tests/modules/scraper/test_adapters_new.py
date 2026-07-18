"""Covers: the 2026-07-18 discovery-expansion adapters (commit 2 of the
approved plan) — Workday CxS (POST + bounded pagination), BambooHR, Breezy,
Arbeitnow, The Muse. Fixture payloads only; zero live network (FakeFetcher).

Anchors: US-JB-01 (scored daily feed breadth), FR-SYS-01 (canonical URLs),
the §4 request-budget discipline (Usage counts every paginated call).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sidecar.modules.scraper import adapters
from sidecar.modules.scraper.adapters import arbeitnow, bamboohr, breezy, themuse, workday
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.types import ScraperError

from .fakes import FakeFetcher, routed

# ---------------------------------------------------------------------------
# Workday
# ---------------------------------------------------------------------------

_WD_URL = "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers"


def _wd_posting(i: int) -> dict:
    return {
        "title": f"Engineer {i}",
        "externalPath": f"/job/Pune-India/Engineer-{i}_JR-{i:05d}",
        "locationsText": "Pune, India",
        "postedOn": "Posted Today",
    }


def _wd_pages(total: int):
    """CxS endpoint fake: pages of ≤20 by the POSTed offset/limit."""

    def respond(_url: str, body: object) -> dict:
        assert isinstance(body, dict)
        offset = int(body["offset"])
        limit = int(body["limit"])
        postings = [_wd_posting(i) for i in range(offset, min(offset + limit, total))]
        return {"total": total, "jobPostings": postings}

    return respond


def test_workday_detect_claims_site_urls() -> None:
    assert workday.detect(SourceEntry(url=_WD_URL)) == "acme/AcmeCareers"
    # No locale segment is fine too.
    assert (
        workday.detect(SourceEntry(url="https://acme.wd103.myworkdayjobs.com/AcmeCareers"))
        == "acme/AcmeCareers"
    )
    assert workday.detect(SourceEntry(url="https://boards.greenhouse.io/acme")) == ""
    assert workday.detect(SourceEntry(url="https://acme.wd5.myworkdayjobs.com/")) == ""
    # Explicit different type never claims.
    assert workday.detect(SourceEntry(url=_WD_URL, type="greenhouse")) == ""


def test_workday_paginates_until_total() -> None:
    fetcher = routed({"/wday/cxs/acme/AcmeCareers/jobs": _wd_pages(45)})()
    jobs = workday.fetch(SourceEntry(url=_WD_URL), fetcher)
    assert len(jobs) == 45
    assert fetcher.usage.internal_calls == 3  # 20 + 20 + 5 — budget visible
    first = jobs[0]
    assert first.title == "Engineer 0"
    assert (
        first.canonical_url
        == "https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/Pune-India/Engineer-0_JR-00000"
    )
    assert first.company == "acme"
    assert first.location == "Pune, India"
    assert first.source_adapter == "workday"
    assert first.description == ""  # not in the list payload — honest empty


def test_workday_page_cap_bounds_request_budget() -> None:
    fetcher = routed({"/wday/cxs/acme/AcmeCareers/jobs": _wd_pages(1000)})()
    jobs = workday.fetch(SourceEntry(url=_WD_URL), fetcher)
    assert len(jobs) == workday.MAX_PAGES * 20
    assert fetcher.usage.internal_calls == workday.MAX_PAGES


def test_workday_bad_payload_raises() -> None:
    fetcher = routed({"/wday/cxs/": {"nope": True}})()
    with pytest.raises(ScraperError, match="jobPostings"):
        workday.fetch(SourceEntry(url=_WD_URL), fetcher)


def test_workday_posted_iso_forms() -> None:
    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    assert workday._posted_iso("Posted Today", now) == "2026-07-18"
    assert workday._posted_iso("Posted Yesterday", now) == "2026-07-17"
    assert workday._posted_iso("Posted 3 Days Ago", now) == "2026-07-15"
    assert workday._posted_iso("Posted 30+ Days Ago", now) == ""  # floor, not a date
    assert workday._posted_iso("", now) == ""


# ---------------------------------------------------------------------------
# BambooHR
# ---------------------------------------------------------------------------


def test_bamboohr_detect_and_fetch() -> None:
    entry = SourceEntry(url="https://acme.bamboohr.com/careers")
    assert bamboohr.detect(entry) == "acme"
    assert bamboohr.detect(SourceEntry(url="https://www.bamboohr.com/pricing")) == ""

    fetcher = routed({"acme.bamboohr.com/careers/list": "bamboohr.json"})()
    jobs = bamboohr.fetch(entry, fetcher)
    assert [j.title for j in jobs] == ["Senior Backend Engineer", "Platform Engineer"]
    assert jobs[0].canonical_url == "https://acme.bamboohr.com/careers/41"
    assert jobs[0].location == "Pune, MH"
    assert jobs[1].location == "Remote"  # isRemote with no city/state
    assert all(j.source_adapter == "bamboohr" for j in jobs)


# ---------------------------------------------------------------------------
# Breezy
# ---------------------------------------------------------------------------


def test_breezy_detect_and_fetch() -> None:
    entry = SourceEntry(url="https://acme.breezy.hr")
    assert breezy.detect(entry) == "acme"
    assert breezy.detect(SourceEntry(url="https://app.breezy.hr/signin")) == ""

    fetcher = routed({"acme.breezy.hr/json": "breezy.json"})()
    jobs = breezy.fetch(entry, fetcher)
    assert len(jobs) == 1  # url-less row skipped
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].canonical_url == "https://acme.breezy.hr/p/abc123-backend-engineer"
    assert jobs[0].location == "Bengaluru, Karnataka, India"  # nested city/state/country
    assert jobs[0].posted_at == "2026-07-10T09:00:00.130Z"


# ---------------------------------------------------------------------------
# Arbeitnow
# ---------------------------------------------------------------------------


def test_arbeitnow_board_keyword_and_fetch() -> None:
    entry = SourceEntry(board="arbeitnow")
    assert arbeitnow.detect(entry) == "arbeitnow.com"

    fetcher = routed({"arbeitnow.com/api/job-board-api": "arbeitnow.json"})()
    jobs = arbeitnow.fetch(entry, fetcher)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Software Engineer (Backend)"
    assert job.company == "Beispiel GmbH"
    assert job.location == "Berlin, Remote"  # remote flag surfaced for filters
    assert job.posted_at == "2026-07-15"  # unix 1752537600 → ISO date
    assert "services" in job.description and "<" not in job.description


# ---------------------------------------------------------------------------
# The Muse
# ---------------------------------------------------------------------------


def test_themuse_board_keyword_and_fetch() -> None:
    entry = SourceEntry(board="themuse")
    assert themuse.detect(entry) == "themuse.com"

    fetcher = routed({"themuse.com/api/public/jobs": "themuse.json"})()
    jobs = themuse.fetch(entry, fetcher)
    assert fetcher.usage.internal_calls == 1  # page_count=1 stops pagination
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Staff Software Engineer"
    assert job.company == "Example Corp"
    assert job.location == "Flexible / Remote, New York, NY"
    assert "platform" in job.description and "<" not in job.description


# ---------------------------------------------------------------------------
# Registry resolution — the new adapters claim before the generic fallback
# ---------------------------------------------------------------------------


def test_registry_resolves_new_adapters() -> None:
    cases = {
        _WD_URL: "workday:acme/AcmeCareers",
        "https://acme.bamboohr.com/careers": "bamboohr:acme",
        "https://acme.breezy.hr": "breezy:acme",
        "https://www.arbeitnow.com/": "arbeitnow:arbeitnow.com",
        "https://www.themuse.com/search": "themuse:themuse.com",
    }
    for url, expected_key in cases.items():
        resolved = adapters.resolve(SourceEntry(url=url))
        assert resolved is not None, url
        _adapter, key = resolved
        assert key == expected_key


def test_fetcher_contract_has_post_json() -> None:
    """Adapters type against http.Fetcher; the fake must keep parity."""
    assert hasattr(FakeFetcher, "post_json")
