"""Covers: company anchoring (FR-NW-02) — `registry/company_anchor.py`.

Job-board / aggregator / ATS-provider hosts must never yield an employer
domain: a shared `domain:naukri.com` resolution key let one employer's confirm
silently poison every company resolved from that board (live 2026-08-02 —
`domain:naukri.com` → Coupang, reused for a Virtusa job).
"""

from __future__ import annotations

import pytest

from sidecar.app.registry.company_anchor import (
    employer_domain,
    registrable_domain,
    resolution_key,
)


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
        # Multi-label board entries (two-level ccTLD suffixes) stay blocked.
        ("https://www.glassdoor.co.in/job-listing/x", "glassdoor", "Acme", "name:acme"),
        ("https://www.seek.com.au/job/7", "seek", "Acme", "name:acme"),
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


def test_two_level_ccTLD_employer_gets_its_own_domain_key() -> None:
    # The naive last-two-label parse collapsed every `.co.in` employer to the
    # shared key `domain:co.in` — the same poison class as the board keys.
    url = "https://careers.tataelxsi.co.in/jobs/1"
    assert employer_domain(url) == "tataelxsi.co.in"
    assert resolution_key(url, "rss", "Tata Elxsi") == "domain:tataelxsi.co.in"


def test_registrable_domain_two_level_suffixes() -> None:
    assert registrable_domain("careers.tataelxsi.co.in") == "tataelxsi.co.in"
    assert registrable_domain("https://www.glassdoor.co.in/x") == "glassdoor.co.in"
    assert registrable_domain("jobs.example.co.uk") == "example.co.uk"
    # Outside the inline set the last-two-label guess is unchanged.
    assert registrable_domain("www.abnormal.ai") == "abnormal.ai"
    assert registrable_domain("bjak.my") == "bjak.my"
    # A bare suffix host stays itself (nothing better to say).
    assert registrable_domain("co.in") == "co.in"
