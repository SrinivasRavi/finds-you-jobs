"""Greenhouse adapter tests — canned real payload, no network.

Covers:
  US-JB-10 — source-adapter attribution on every row
  Track M3 spec — per-source adapters over public JSON APIs
"""

from __future__ import annotations

import pytest

from sidecar.modules.scraper.adapters import greenhouse
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.types import ScraperError

from .fakes import routed


def test_detect_claims_board_url_shapes():
    assert greenhouse.detect(SourceEntry(url="https://boards.greenhouse.io/gleanwork")) == (
        "gleanwork"
    )
    assert greenhouse.detect(
        SourceEntry(url="https://job-boards.greenhouse.io/gleanwork/jobs/4006731005")
    ) == ("gleanwork")
    assert greenhouse.detect(
        SourceEntry(url="https://boards-api.greenhouse.io/v1/boards/gleanwork/jobs")
    ) == ("gleanwork")
    assert greenhouse.detect(SourceEntry(url="https://jobs.lever.co/cred")) == ""
    assert greenhouse.detect(SourceEntry(url="https://boards.greenhouse.io/")) == ""


def test_detect_respects_explicit_type():
    assert greenhouse.detect(SourceEntry(url="https://boards.greenhouse.io/x", type="rss")) == ""


def test_fetch_normalizes_real_payload():
    fetcher = routed({"boards-api.greenhouse.io/v1/boards/gleanwork/jobs": "greenhouse.json"})()
    jobs = greenhouse.fetch(
        SourceEntry(url="https://boards.greenhouse.io/gleanwork", company="Glean"), fetcher
    )
    assert jobs and all(j.source_adapter == "greenhouse" for j in jobs)
    first = jobs[0]
    assert first.title
    assert first.canonical_url.startswith("https://job-boards.greenhouse.io/gleanwork/jobs/")
    assert first.company == "Glean"  # config override wins
    assert first.location
    # content=true carries the JD body in the same list request (decoded to text)
    assert first.description
    assert "<" not in first.description  # entity-encoded HTML fully stripped
    assert first.posted_at  # first_published carried through
    assert fetcher.usage.internal_calls == 1  # one list request, never per-job


def test_fetch_company_falls_back_to_payload_then_slug():
    fetcher = routed({"/boards/gleanwork/jobs": "greenhouse.json"})()
    jobs = greenhouse.fetch(SourceEntry(url="https://boards.greenhouse.io/gleanwork"), fetcher)
    assert jobs[0].company == "Glean"  # payload company_name


def test_fetch_bad_payload_is_typed_error():
    fetcher = routed({"/boards/broken/jobs": {"nope": True}})()
    with pytest.raises(ScraperError) as ei:
        greenhouse.fetch(SourceEntry(url="https://boards.greenhouse.io/broken"), fetcher)
    assert "[greenhouse]" in str(ei.value)
