"""Covers: company anchoring (FR-NW-02) — `registry/company_anchor.py`.

Job-board / aggregator / ATS-provider hosts must never yield an employer
domain: a shared `domain:naukri.com` resolution key let one employer's confirm
silently poison every company resolved from that board (live 2026-08-02 —
`domain:naukri.com` → Coupang, reused for a Virtusa job).
"""

from __future__ import annotations

import pytest

from sidecar.app.registry.company_anchor import employer_domain, resolution_key


@pytest.mark.parametrize(
    ("url", "adapter", "company", "expected_key"),
    [
        # Board hosts (www./country subdomains included) → no domain anchor,
        # key falls to name:.
        (
            "https://www.naukri.com/job-listings-backend-virtusa-1",
            "naukri", "Virtusa", "name:virtusa",
        ),
        ("https://www.linkedin.com/jobs/view/42", "linkedin", "Acme", "name:acme"),
        ("https://www.foundit.in/job/9", "foundit", "Acme", "name:acme"),
        ("https://in.indeed.com/viewjob?jk=abc", "indeed", "Acme", "name:acme"),
        # Multi-label board entry the naive registrable_domain can't name.
        ("https://www.glassdoor.co.in/job-listing/x", "glassdoor", "Acme", "name:acme"),
        # Tenant-subdomain ATS providers: registrable domain is the provider's.
        ("https://acme.wd5.myworkdayjobs.com/en-US/ext/job/1", "workday", "Acme", "name:acme"),
        ("https://acme.bamboohr.com/careers/7", "bamboohr", "Acme", "name:acme"),
    ],
)
def test_job_board_hosts_never_anchor_a_domain(
    url: str, adapter: str, company: str, expected_key: str
) -> None:
    assert employer_domain(url) == ""
    assert resolution_key(url, adapter, company) == expected_key


def test_slug_first_ats_still_yields_slug_key() -> None:
    url = "https://boards.greenhouse.io/6sense/jobs/123"
    assert employer_domain(url) == ""
    assert resolution_key(url, "greenhouse", "6sense") == "greenhouse:6sense"


def test_employer_site_still_yields_domain_key() -> None:
    url = "https://www.abnormal.ai/careers/123"
    assert employer_domain(url) == "abnormal.ai"
    assert resolution_key(url, "greenhouse", "Abnormal Security") == "domain:abnormal.ai"


def test_board_domain_as_infix_is_not_blocked() -> None:
    # Suffix match is label-anchored: a lookalike employer host that merely
    # contains a board name is still an employer domain.
    assert employer_domain("https://naukri.com.example.com/careers/1") == "example.com"
