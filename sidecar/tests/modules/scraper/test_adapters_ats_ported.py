"""Ported zero-token ATS adapters — SmartRecruiters / Recruitee / Teamtailor /
Personio, against canned real-shaped payloads (no live network).

Covers:
  US-JB-10 — source-adapter attribution on every row
  Track M3 spec — per-source adapters over public JSON/RSS/XML APIs
  architecture §7 decision (2026-07-13) — the four providers moved from "Later"
  to built, ported from career-ops (MIT).
"""

from __future__ import annotations

import pytest

from sidecar.modules.scraper.adapters import personio, recruitee, smartrecruiters, teamtailor
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.types import ScraperError

from .fakes import FakeFetcher, routed

# --- smartrecruiters -------------------------------------------------------


def test_smartrecruiters_detect_claims_careers_hosts():
    assert smartrecruiters.detect(
        SourceEntry(url="https://careers.smartrecruiters.com/Continental")
    ) == "Continental"
    assert smartrecruiters.detect(
        SourceEntry(url="https://jobs.smartrecruiters.com/Continental/postings")
    ) == "Continental"
    assert smartrecruiters.detect(SourceEntry(url="https://jobs.lever.co/cred")) == ""
    assert smartrecruiters.detect(SourceEntry(url="https://careers.smartrecruiters.com/")) == ""


def test_smartrecruiters_detect_respects_explicit_type():
    assert smartrecruiters.detect(
        SourceEntry(url="https://careers.smartrecruiters.com/Continental", type="rss")
    ) == ""


def test_smartrecruiters_fetch_normalizes_and_rewrites_url():
    fetcher = routed(
        {"api.smartrecruiters.com/v1/companies/Continental/postings": "smartrecruiters.json"}
    )()
    jobs = smartrecruiters.fetch(
        SourceEntry(url="https://careers.smartrecruiters.com/Continental"), fetcher
    )
    assert jobs and all(j.source_adapter == "smartrecruiters" for j in jobs)
    first = jobs[0]
    assert first.title == "Senior Backend Engineer"
    # ref (api host) rewritten to the public careers URL
    assert first.canonical_url == (
        "https://jobs.smartrecruiters.com/Continental/postings/744000012345678"
    )
    assert first.company == "Continental"  # slug fallback
    assert first.location == "Bengaluru, Karnataka, in"
    assert first.posted_at.startswith("2026-06-15")
    # remote + fullLocation on the second row
    assert jobs[1].location == "Anywhere, India, Remote"


def test_smartrecruiters_location_does_not_double_remote():
    # Live SmartRecruiters bakes REMOTE into fullLocation; we must not append it
    # again (the 2026-07-13 live-scan cosmetic bug: "Poland, REMOTE, Poland, Remote").
    payload = {"content": [{
        "id": "1", "name": "Role",
        "ref": "https://api.smartrecruiters.com/v1/companies/Acme/postings/1",
        "location": {"fullLocation": "Poland, REMOTE, Poland", "remote": True},
    }]}
    fetcher = routed({"api.smartrecruiters.com/v1/companies/Acme/postings": payload})()
    entry = SourceEntry(url="https://careers.smartrecruiters.com/Acme")
    jobs = smartrecruiters.fetch(entry, fetcher)
    assert jobs[0].location == "Poland, REMOTE, Poland"  # no second ", Remote"


def test_smartrecruiters_company_override_wins():
    fetcher = routed(
        {"api.smartrecruiters.com/v1/companies/Continental/postings": "smartrecruiters.json"}
    )()
    jobs = smartrecruiters.fetch(
        SourceEntry(url="https://careers.smartrecruiters.com/Continental", company="Conti"),
        fetcher,
    )
    assert jobs[0].company == "Conti"


def test_smartrecruiters_pagination_stops_on_short_page():
    # A full first page (100 rows) then a short second page → exactly two requests.
    full = {"content": [
        {"id": str(i), "name": f"Role {i}",
         "ref": f"https://api.smartrecruiters.com/v1/companies/Acme/postings/{i}",
         "location": {"city": "Pune"}} for i in range(100)
    ]}
    short = {"content": [
        {"id": "x", "name": "Last",
         "ref": "https://api.smartrecruiters.com/v1/companies/Acme/postings/x",
         "location": {"city": "Pune"}}
    ]}

    class Paged(FakeFetcher):
        calls = 0

        def get_json(self, url: str, headers: dict[str, str] | None = None) -> object:
            Paged.calls += 1
            self.usage.internal_calls += 1
            return full if "offset=0" in url else short

    entry = SourceEntry(url="https://careers.smartrecruiters.com/Acme")
    jobs = smartrecruiters.fetch(entry, Paged())
    assert len(jobs) == 101
    assert Paged.calls == 2  # short second page halts the loop


def test_smartrecruiters_bad_payload_is_typed_error():
    fetcher = routed(
        {"api.smartrecruiters.com/v1/companies/broken/postings": {"nope": True}}
    )()
    with pytest.raises(ScraperError) as ei:
        smartrecruiters.fetch(
            SourceEntry(url="https://careers.smartrecruiters.com/broken"), fetcher
        )
    assert "[smartrecruiters]" in str(ei.value)


# --- recruitee -------------------------------------------------------------


def test_recruitee_detect_claims_tenant_subdomain():
    assert recruitee.detect(SourceEntry(url="https://acme.recruitee.com")) == "acme.recruitee.com"
    assert recruitee.detect(
        SourceEntry(url="https://acme.recruitee.com/o/backend")
    ) == "acme.recruitee.com"
    assert recruitee.detect(SourceEntry(url="https://recruitee.com")) == ""
    assert recruitee.detect(SourceEntry(url="https://jobs.lever.co/cred")) == ""


def test_recruitee_fetch_normalizes_real_payload():
    fetcher = routed({"acme.recruitee.com/api/offers/": "recruitee.json"})()
    jobs = recruitee.fetch(SourceEntry(url="https://acme.recruitee.com"), fetcher)
    assert jobs and all(j.source_adapter == "recruitee" for j in jobs)
    first = jobs[0]
    assert first.title == "Full-stack Engineer"
    assert first.canonical_url == "https://acme.recruitee.com/o/full-stack-engineer"
    assert first.company == "acme"  # slug fallback
    assert first.location == "Amsterdam, Netherlands"
    assert first.posted_at.startswith("2026-06-10")
    # explicit location field wins; url falls back to `url` when careers_url absent
    assert jobs[1].location == "Remote (EU)"
    assert jobs[1].canonical_url == "https://acme.recruitee.com/api/offers/7654321"
    assert fetcher.usage.internal_calls == 1  # one list request


def test_recruitee_normalizes_utc_published_at():
    # Live Recruitee stamps published_at as "YYYY-MM-DD HH:MM:SS UTC" (not ISO),
    # which the freshness filter can't parse (2026-07-13 live-scan finding).
    payload = {"offers": [{
        "title": "Role", "careers_url": "https://acme.recruitee.com/o/role",
        "location": "Remote", "published_at": "2026-07-03 12:37:16 UTC",
    }]}
    fetcher = routed({"acme.recruitee.com/api/offers/": payload})()
    jobs = recruitee.fetch(SourceEntry(url="https://acme.recruitee.com"), fetcher)
    assert jobs[0].posted_at == "2026-07-03T12:37:16+00:00"


def test_recruitee_bad_payload_is_typed_error():
    fetcher = routed({"broken.recruitee.com/api/offers/": {"nope": True}})()
    with pytest.raises(ScraperError) as ei:
        recruitee.fetch(SourceEntry(url="https://broken.recruitee.com"), fetcher)
    assert "[recruitee]" in str(ei.value)


# --- teamtailor ------------------------------------------------------------


def test_teamtailor_detect_claims_tenant_and_normalizes():
    assert teamtailor.detect(SourceEntry(url="https://acme.teamtailor.com")) == "acme"
    assert teamtailor.detect(
        SourceEntry(url="https://acme.teamtailor.com/jobs/1-role")
    ) == "acme"
    assert teamtailor.detect(SourceEntry(url="https://teamtailor.com")) == ""
    assert teamtailor.detect(SourceEntry(url="https://jobs.lever.co/cred")) == ""


def test_teamtailor_fetch_parses_feed():
    fetcher = routed({"acme.teamtailor.com/jobs.rss": "teamtailor.rss"})()
    jobs = teamtailor.fetch(
        SourceEntry(url="https://acme.teamtailor.com", company="Acme"), fetcher
    )
    assert len(jobs) == 2  # the third item (no link) is skipped
    assert all(j.source_adapter == "teamtailor" for j in jobs)
    first = jobs[0]
    assert first.title == "Backend Engineer"
    assert first.canonical_url == "https://acme.teamtailor.com/jobs/111-backend-engineer"
    assert first.company == "Acme"
    assert first.location == "Stockholm, Sweden"
    assert "APIs" in first.description and "<" not in first.description  # HTML stripped
    assert first.posted_at.startswith("2026-06-15")
    assert jobs[1].location == ""  # no location element on the second item


def test_teamtailor_company_falls_back_to_slug():
    fetcher = routed({"acme.teamtailor.com/jobs.rss": "teamtailor.rss"})()
    jobs = teamtailor.fetch(SourceEntry(url="https://acme.teamtailor.com"), fetcher)
    assert jobs[0].company == "acme"


def test_teamtailor_bad_xml_is_typed_error():
    fetcher = routed({"broken.teamtailor.com/jobs.rss": "not_xml.txt"})()
    with pytest.raises(ScraperError) as ei:
        teamtailor.fetch(SourceEntry(url="https://broken.teamtailor.com"), fetcher)
    assert "[teamtailor]" in str(ei.value)


# --- personio --------------------------------------------------------------


def test_personio_detect_claims_de_and_com():
    assert personio.detect(
        SourceEntry(url="https://acme.jobs.personio.de")
    ) == "acme.jobs.personio.de"
    assert personio.detect(
        SourceEntry(url="https://acme.jobs.personio.com/positions")
    ) == "acme.jobs.personio.com"
    assert personio.detect(SourceEntry(url="https://jobs.personio.de")) == ""
    assert personio.detect(SourceEntry(url="https://acme.personio.de")) == ""


def test_personio_fetch_parses_xml_and_survives_position_in_description():
    fetcher = routed({"acme.jobs.personio.de/xml": "personio.xml"})()
    jobs = personio.fetch(
        SourceEntry(url="https://acme.jobs.personio.de", company="Acme"), fetcher
    )
    # The second position has a non-numeric id → skipped. The </position> literal
    # inside the first job's CDATA description must NOT truncate parsing.
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Senior Softwareentwickler (m/w/d)"
    assert job.canonical_url == "https://acme.jobs.personio.de/job/987654"
    assert job.company == "Acme"
    assert job.location == "München, Berlin"  # primary + additionalOffices, de-duped
    assert "Baue Dienste" in job.description and "</position>" not in job.description
    assert job.posted_at.startswith("2026-06-01")
    assert fetcher.usage.internal_calls == 1


def test_personio_bad_xml_is_typed_error():
    fetcher = routed({"broken.jobs.personio.de/xml": "not_xml.txt"})()
    with pytest.raises(ScraperError) as ei:
        personio.fetch(SourceEntry(url="https://broken.jobs.personio.de"), fetcher)
    assert "[personio]" in str(ei.value)
