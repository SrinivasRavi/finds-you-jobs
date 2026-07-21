"""Scraper core tests — canonicalization, filters, quality, config. No network.

Covers:
  US-SYS-01 / FR-SYS-01 — canonical-URL dedup key
  US-JB-01 — feed relevance via title/location filters
  Track M3 spec — trust/quality checks at ingest (annotate, rank-don't-gate)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sidecar.modules.scraper.canonical import canonicalize_url
from sidecar.modules.scraper.config import load_portals, parse_portals
from sidecar.modules.scraper.filters import (
    keyword_match,
    passes_company,
    passes_content,
    passes_location,
    passes_title,
)
from sidecar.modules.scraper.quality import assess, is_structurally_broken
from sidecar.modules.scraper.types import NormalizedJob, ScanPrefs, ScraperError

# --- canonical ---


def test_canonical_lowercases_and_strips_tracking():
    assert (
        canonicalize_url("HTTPS://Jobs.Lever.CO/cred/abc-123/?utm_source=x&ref=hn&gh_src=abc")
        == "https://jobs.lever.co/cred/abc-123"
    )


def test_canonical_keeps_meaningful_query_and_sorts():
    url = canonicalize_url("https://www.observe.ai/position?zz=1&gh_jid=4173623008")
    assert url == "https://www.observe.ai/position?gh_jid=4173623008&zz=1"


def test_canonical_drops_fragment_and_default_port():
    assert (
        canonicalize_url("https://example.com:443/jobs/1#apply") == "https://example.com/jobs/1"
    )


def test_canonical_rejects_non_http():
    assert canonicalize_url("ftp://example.com/x") == ""
    assert canonicalize_url("not a url") == ""


def test_canonical_same_posting_same_key():
    a = canonicalize_url("https://job-boards.greenhouse.io/gleanwork/jobs/4006731005?gh_src=x")
    b = canonicalize_url("https://job-boards.greenhouse.io/gleanwork/jobs/4006731005/")
    assert a == b != ""


# --- filters (career-ops #1101/#1169 word-boundary lessons) ---


def test_keyword_word_boundary_short_acronym():
    assert keyword_match("CTO wanted", ["cto"])
    assert not keyword_match("Director of things", ["cto"])  # no substring hit inside "direCTOr"


def test_keyword_multiword_matches_across_whitespace():
    assert keyword_match("Senior Software  Engineer, Backend", ["software engineer"])
    assert not keyword_match("Software Test Engineer", ["software engineer"])


def test_title_block_wins_over_allow():
    prefs = ScanPrefs(title_allow=["engineer"], title_block=["staff"])
    assert passes_title("Backend Engineer", prefs)
    assert not passes_title("Staff Engineer", prefs)


def test_title_empty_allow_passes_everything():
    assert passes_title("Anything At All", ScanPrefs())


def test_location_india_does_not_match_indianapolis():
    prefs = ScanPrefs(location_allow=["india"])
    assert passes_location("Bengaluru, India", prefs)
    assert not passes_location("Indianapolis, IN, USA", prefs)


def test_location_always_allow_rescues_blocked_multi_location():
    prefs = ScanPrefs(
        location_allow=["new york"],
        location_block=["india"],
        location_always_allow=["remote"],
    )
    assert passes_location("Remote, New York or India", prefs)
    assert not passes_location("Mumbai, India", prefs)


def test_location_unknown_passes():
    assert passes_location("", ScanPrefs(location_allow=["india"]))


def test_company_block_excludes_matching_company():
    prefs = ScanPrefs(company_block=["Meta"])
    assert not passes_company("Meta", prefs)
    assert passes_company("Metabase", prefs)  # word-boundary — no substring hit


def test_company_unknown_passes():
    assert passes_company("", ScanPrefs(company_block=["Meta"]))


def test_content_block_wins_over_allow():
    prefs = ScanPrefs(content_allow=["python"], content_block=["unpaid"])
    assert passes_content("Python backend role, full pay", prefs)
    assert not passes_content("Python role, unpaid trial period", prefs)


def test_content_empty_allow_passes_everything():
    assert passes_content("Anything at all", ScanPrefs())


def test_content_unknown_description_passes():
    assert passes_content("", ScanPrefs(content_allow=["python"]))


# --- quality (annotate, never drop valid rows) ---


def _job(**kw) -> NormalizedJob:
    base: dict[str, Any] = {"title": "Backend Engineer", "canonical_url": "https://x.co/jobs/1"}
    base.update(kw)
    return NormalizedJob(**base)


def test_quality_clean_job_scores_high_and_keeps_row():
    job = _job(location="Pune, India", posted_at="2026-07-01T00:00:00+00:00")
    assess(job, now=datetime(2026, 7, 7, tzinfo=UTC))
    assert job.trust_score >= 90
    assert "no-location" not in job.trust_flags


def test_quality_red_flag_term_deducts_but_never_drops():
    job = _job(description="Great role, equity only for the first year")
    assess(job)
    assert any(f.startswith("red-flag-term:") for f in job.trust_flags)
    assert 0 <= job.trust_score < 100
    assert not is_structurally_broken(job)  # rank, don't gate


def test_quality_stale_and_insecure_flags():
    job = _job(
        canonical_url="http://x.co/jobs/1",
        posted_at="2026-01-01T00:00:00+00:00",
    )
    assess(job, now=datetime(2026, 7, 7, tzinfo=UTC))
    assert "insecure-url" in job.trust_flags
    assert "stale-posting" in job.trust_flags


def test_structurally_broken_rows_detected():
    assert is_structurally_broken(NormalizedJob(title="", canonical_url="https://x.co/1"))
    assert is_structurally_broken(NormalizedJob(title="X", canonical_url=""))
    assert not is_structurally_broken(NormalizedJob(title="X", canonical_url="https://x.co/1"))


# --- config ---


def test_load_portals_toml_roundtrip(tmp_path):
    p = tmp_path / "portals.toml"
    p.write_text(
        """
[[sources]]
url = "https://boards.greenhouse.io/gleanwork"
company = "Glean"

[[sources]]
board = "remoteok"

[filters.title]
allow = ["software engineer"]
[filters.location]
allow = ["india"]
always_allow = ["remote"]
[filters.company]
block = ["Example Corp"]
[filters.content]
block = ["unpaid internship"]
[scan]
max_age_days = 30
"""
    )
    config = load_portals(p)
    assert config.sources[0].url.endswith("/gleanwork")
    assert config.sources[0].company == "Glean"
    assert config.sources[1].board == "remoteok"
    assert config.prefs.title_allow == ["software engineer"]
    assert config.prefs.location_always_allow == ["remote"]
    assert config.prefs.company_block == ["Example Corp"]
    assert config.prefs.content_block == ["unpaid internship"]
    assert config.prefs.max_age_days == 30
    assert config.prefs.per_source_cap == 0  # default: never self-throttle


def test_portals_config_errors_are_typed_and_verbatim(tmp_path):
    with pytest.raises(ScraperError) as ei:
        parse_portals({"sources": []})
    assert "[portals-config]" in str(ei.value)

    bad = tmp_path / "bad.toml"
    bad.write_text("[[sources]]\nname_only = true\n")
    with pytest.raises(ScraperError) as ei:
        load_portals(bad)
    assert "needs `url` or `board`" in str(ei.value)

    with pytest.raises(ScraperError):
        load_portals(tmp_path / "missing.toml")
