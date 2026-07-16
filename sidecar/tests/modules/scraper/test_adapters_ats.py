"""ATS source-adapter tests — Lever / Ashby / Workable, canned real payloads.

Covers:
  US-JB-10 — source-adapter attribution on every row
  Track M3 spec — per-source adapters over public JSON APIs
"""

from __future__ import annotations

import pytest

from sidecar.modules.scraper.adapters import ashby, lever, workable
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.types import ScraperError

from .fakes import routed

# --- lever -----------------------------------------------------------------


def test_lever_detect_claims_url_shapes():
    assert lever.detect(SourceEntry(url="https://jobs.lever.co/cred")) == "cred"
    assert lever.detect(
        SourceEntry(url="https://jobs.lever.co/cred/8c5a9539-2ef0-45f3")
    ) == "cred"
    assert lever.detect(SourceEntry(url="https://jobs.eu.lever.co/spotify")) == "spotify"
    assert lever.detect(SourceEntry(url="https://boards.greenhouse.io/x")) == ""
    assert lever.detect(SourceEntry(url="https://jobs.lever.co/")) == ""


def test_lever_detect_respects_explicit_type():
    assert lever.detect(SourceEntry(url="https://jobs.lever.co/cred", type="rss")) == ""


def test_lever_fetch_normalizes_real_payload():
    fetcher = routed({"api.lever.co/v0/postings/cred": "lever.json"})()
    jobs = lever.fetch(
        SourceEntry(url="https://jobs.lever.co/cred", company="CRED"), fetcher
    )
    assert jobs and all(j.source_adapter == "lever" for j in jobs)
    first = jobs[0]
    assert first.title
    assert first.canonical_url.startswith("https://jobs.lever.co/cred/")
    assert first.company == "CRED"  # config override wins
    assert first.location
    assert first.description  # descriptionPlain ships free in the list payload
    assert first.posted_at
    assert fetcher.usage.internal_calls == 1  # one list request, never per-job


def test_lever_fetch_company_falls_back_to_slug():
    fetcher = routed({"api.lever.co/v0/postings/cred": "lever.json"})()
    jobs = lever.fetch(SourceEntry(url="https://jobs.lever.co/cred"), fetcher)
    assert jobs[0].company == "cred"  # no company on posting → slug


def test_lever_eu_host_and_created_ms_to_iso():
    payload = [
        {
            "text": "Backend Engineer",
            "hostedUrl": "https://jobs.eu.lever.co/acme/1",
            "categories": {"location": "Berlin"},
            "createdAt": 1609459200000,
            "descriptionPlain": "do backend things",
            "salaryRange": {"min": 100000, "max": 150000, "currency": "EUR"},
        }
    ]
    fetcher = routed({"api.eu.lever.co/v0/postings/acme": payload})()
    jobs = lever.fetch(SourceEntry(url="https://jobs.eu.lever.co/acme"), fetcher)
    assert jobs[0].posted_at == "2021-01-01T00:00:00+00:00"
    assert jobs[0].salary == "100000–150000 EUR"


def test_lever_location_falls_back_to_all_locations():
    payload = [
        {
            "text": "Analyst",
            "hostedUrl": "https://jobs.lever.co/acme/2",
            "categories": {"allLocations": ["London", "Remote"]},
            "createdAt": 1609459200000,
        }
    ]
    fetcher = routed({"api.lever.co/v0/postings/acme": payload})()
    jobs = lever.fetch(SourceEntry(url="https://jobs.lever.co/acme"), fetcher)
    assert jobs[0].location == "London; Remote"
    assert jobs[0].salary == ""  # no salaryRange


def test_lever_fetch_bad_payload_is_typed_error():
    fetcher = routed({"api.lever.co/v0/postings/broken": {"nope": True}})()
    with pytest.raises(ScraperError) as ei:
        lever.fetch(SourceEntry(url="https://jobs.lever.co/broken"), fetcher)
    assert "[lever]" in str(ei.value)


# --- ashby -----------------------------------------------------------------


def test_ashby_detect_claims_url_shapes():
    assert ashby.detect(SourceEntry(url="https://jobs.ashbyhq.com/sarvam")) == "sarvam"
    assert ashby.detect(
        SourceEntry(url="https://jobs.ashbyhq.com/sarvam/03a26f63")
    ) == "sarvam"
    assert ashby.detect(SourceEntry(url="https://jobs.lever.co/cred")) == ""
    assert ashby.detect(SourceEntry(url="https://jobs.ashbyhq.com/")) == ""


def test_ashby_detect_respects_explicit_type():
    assert ashby.detect(SourceEntry(url="https://jobs.ashbyhq.com/sarvam", type="rss")) == ""


def test_ashby_fetch_normalizes_real_payload():
    fetcher = routed(
        {"api.ashbyhq.com/posting-api/job-board/sarvam": "ashby.json"}
    )()
    jobs = ashby.fetch(
        SourceEntry(url="https://jobs.ashbyhq.com/sarvam", company="Sarvam"), fetcher
    )
    assert jobs and all(j.source_adapter == "ashby" for j in jobs)
    first = jobs[0]
    assert first.title
    assert first.canonical_url.startswith("https://jobs.ashbyhq.com/sarvam/")
    assert first.company == "Sarvam"  # config override wins
    assert first.location
    assert first.posted_at
    assert first.salary == ""  # fixture compensation summaries are null
    assert fetcher.usage.internal_calls == 1  # one list request, never per-job


def test_ashby_fetch_company_falls_back_to_org():
    fetcher = routed(
        {"api.ashbyhq.com/posting-api/job-board/sarvam": "ashby.json"}
    )()
    jobs = ashby.fetch(SourceEntry(url="https://jobs.ashbyhq.com/sarvam"), fetcher)
    assert jobs[0].company == "sarvam"  # no company on posting → org


def test_ashby_skips_unlisted_and_handles_remote_and_missing_compensation():
    payload = {
        "jobs": [
            {
                "title": "Listed Remote Role",
                "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                "location": "Bengaluru",
                "secondaryLocations": [{"location": "Pune"}],
                "isListed": True,
                "isRemote": True,
                "publishedAt": "2026-06-01T00:00:00+00:00",
                # no compensation key at all
            },
            {
                "title": "Hidden Role",
                "jobUrl": "https://jobs.ashbyhq.com/acme/2",
                "location": "Delhi",
                "isListed": False,
            },
        ]
    }
    fetcher = routed({"api.ashbyhq.com/posting-api/job-board/acme": payload})()
    jobs = ashby.fetch(SourceEntry(url="https://jobs.ashbyhq.com/acme"), fetcher)
    assert len(jobs) == 1  # unlisted posting skipped
    assert jobs[0].location == "Remote; Bengaluru; Pune"
    assert jobs[0].salary == ""  # missing compensation guarded


def test_ashby_fetch_bad_payload_is_typed_error():
    fetcher = routed({"api.ashbyhq.com/posting-api/job-board/broken": {"nope": True}})()
    with pytest.raises(ScraperError) as ei:
        ashby.fetch(SourceEntry(url="https://jobs.ashbyhq.com/broken"), fetcher)
    assert "[ashby]" in str(ei.value)


# --- workable --------------------------------------------------------------


def test_workable_detect_claims_url_shapes():
    assert workable.detect(
        SourceEntry(url="https://apply.workable.com/gsstech-group")
    ) == "gsstech-group"
    assert workable.detect(SourceEntry(url="https://apply.workable.com/j/3766624E44")) == ""
    assert workable.detect(SourceEntry(url="https://apply.workable.com/api/v1/x")) == ""
    assert workable.detect(SourceEntry(url="https://jobs.lever.co/cred")) == ""
    assert workable.detect(SourceEntry(url="https://apply.workable.com/")) == ""


def test_workable_detect_respects_explicit_type():
    assert workable.detect(
        SourceEntry(url="https://apply.workable.com/gsstech-group", type="rss")
    ) == ""


def test_workable_fetch_normalizes_real_payload():
    fetcher = routed(
        {"apply.workable.com/api/v1/widget/accounts/gsstech-group": "workable.json"}
    )()
    jobs = workable.fetch(
        SourceEntry(url="https://apply.workable.com/gsstech-group"), fetcher
    )
    assert jobs and all(j.source_adapter == "workable" for j in jobs)
    first = jobs[0]
    assert first.title
    assert first.canonical_url.startswith("https://apply.workable.com/")
    assert first.company == "GSSTech Group"  # payload account name
    assert first.location
    assert first.posted_at
    # details=true carries the JD body in the same list request (HTML → text)
    assert first.description
    assert "<" not in first.description
    assert fetcher.usage.internal_calls == 1  # one list request, never per-job


def test_workable_company_override_wins():
    fetcher = routed(
        {"apply.workable.com/api/v1/widget/accounts/gsstech-group": "workable.json"}
    )()
    jobs = workable.fetch(
        SourceEntry(url="https://apply.workable.com/gsstech-group", company="GSS"), fetcher
    )
    assert jobs[0].company == "GSS"  # config override wins over payload name


def test_workable_telecommuting_remote_handling():
    payload = {
        "name": "Acme",
        "jobs": [
            {
                "title": "Remote Only",
                "url": "https://apply.workable.com/j/AAA",
                "telecommuting": True,
                "published_on": "2026-01-01",
            },
            {
                "title": "Remote Plus Location",
                "url": "https://apply.workable.com/j/BBB",
                "telecommuting": True,
                "city": "Bengaluru",
                "country": "India",
                "published_on": "2026-01-01",
            },
        ],
    }
    fetcher = routed({"apply.workable.com/api/v1/widget/accounts/acme": payload})()
    jobs = workable.fetch(SourceEntry(url="https://apply.workable.com/acme"), fetcher)
    assert jobs[0].location == "Remote"
    assert jobs[1].location == "Remote; Bengaluru, India"
    assert jobs[0].company == "Acme"  # payload name fallback


def test_workable_fetch_bad_payload_is_typed_error():
    fetcher = routed({"apply.workable.com/api/v1/widget/accounts/broken": {"nope": True}})()
    with pytest.raises(ScraperError) as ei:
        workable.fetch(SourceEntry(url="https://apply.workable.com/broken"), fetcher)
    assert "[workable]" in str(ei.value)
