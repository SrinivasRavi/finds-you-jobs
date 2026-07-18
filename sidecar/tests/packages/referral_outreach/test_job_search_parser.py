"""Covers: the LinkedIn logged-in jobs-search parser (discovery-expansion #6).

The fixture is a slimmed + redacted capture of a REAL logged-in
`voyagerJobsDashJobCards` response (derived live 2026-07-18 — see
`docs/internal/discovery.md`). Pins the parse shape so a LinkedIn response
change is caught in CI, without any live browser.
"""

from __future__ import annotations

import json
from pathlib import Path

from sidecar.packages.referral_outreach.upstream.jobs import parse_job_search_response

FIXTURE = Path(__file__).parent / "fixtures" / "job_search_response.json"


def test_parses_real_capture_in_element_order() -> None:
    data = json.loads(FIXTURE.read_text())
    out = parse_job_search_response(data)

    assert out["total"] == data["data"]["paging"]["total"]
    jobs = out["jobs"]
    assert len(jobs) == 3

    # Order follows data.elements (LinkedIn's relevance order), not included order.
    element_ids = [
        (e["jobCardUnion"]["*jobPostingCard"].split("(")[1].split(",")[0])
        for e in data["data"]["elements"]
    ]
    assert [j["id"] for j in jobs] == element_ids

    first = jobs[0]
    assert first["url"] == f"https://www.linkedin.com/jobs/view/{first['id']}"
    assert first["title"] and " " not in first["title"]  # trimmed nbsp
    assert first["company"]
    assert first["location"]
    assert first["title"] == first["title"].strip()


def test_skips_cards_without_id_or_title() -> None:
    data = {
        "data": {
            "elements": [
                {"jobCardUnion": {"*jobPostingCard": "urn:li:fsd_jobPostingCard:(1,JOBS_SEARCH)"}},
                {"jobCardUnion": {"*jobPostingCard": "urn:li:fsd_jobPostingCard:(2,JOBS_SEARCH)"}},
            ],
            "paging": {"total": 2},
        },
        "included": [
            {"$type": "com.linkedin.voyager.dash.jobs.JobPostingCard",
             "entityUrn": "urn:li:fsd_jobPostingCard:(1,JOBS_SEARCH)",
             "jobPostingUrn": "urn:li:fsd_jobPosting:1",
             "title": {"text": "Real Job"}, "primaryDescription": {"text": "Co"}},
            # No title → skipped, not crashed.
            {"$type": "com.linkedin.voyager.dash.jobs.JobPostingCard",
             "entityUrn": "urn:li:fsd_jobPostingCard:(2,JOBS_SEARCH)",
             "jobPostingUrn": "urn:li:fsd_jobPosting:2",
             "title": {"text": ""}, "primaryDescription": {"text": "Co"}},
        ],
    }
    out = parse_job_search_response(data)
    assert [j["id"] for j in out["jobs"]] == ["1"]


def test_empty_and_malformed_inputs_are_safe() -> None:
    assert parse_job_search_response({}) == {"jobs": [], "total": 0}
    assert parse_job_search_response({"data": {}, "included": []}) == {"jobs": [], "total": 0}
    assert parse_job_search_response({"data": None}) == {"jobs": [], "total": 0}  # type: ignore[arg-type]
