# voyager_py/tests/test_discovery_filter.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Discovery's URN-scoped search URL + the enrich-time re-verify filter — the
L1/L2 correctness core (docs/referral-outreach-discovery-design.md §2). Pure;
no browser, no network."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from sidecar.packages.referral_outreach.upstream.discovery import (
    _people_search_url,
    _profile_matches_company,
)


def _qs(url: str) -> dict:
    return parse_qs(urlparse(url).query)


def test_people_search_url_scopes_by_current_company_when_urn_given():
    url = _people_search_url("Hopper", company_urn="urn:li:fsd_company:162479")
    qs = _qs(url)
    assert qs["currentCompany"] == ['["162479"]']  # JSON company-id array facet
    assert "keywords" not in qs  # L1: never fall back to the name keyword


def test_people_search_url_falls_back_to_keyword_without_urn():
    url = _people_search_url("Hopper")
    qs = _qs(url)
    assert qs["keywords"] == ["Hopper"]
    assert "currentCompany" not in qs


def test_people_search_url_ignores_non_company_urn():
    # A malformed/non-company urn must not become a garbage facet — fall back.
    url = _people_search_url("Hopper", company_urn="urn:li:fsd_profile:jane")
    assert "currentCompany" not in _qs(url)
    assert _qs(url)["keywords"] == ["Hopper"]


def _profile(company_urn=None, company_name=None):
    pos = {}
    if company_urn is not None:
        pos["company_urn"] = company_urn
    if company_name is not None:
        pos["company_name"] = company_name
    return {"current_position": pos}


def test_reverify_matches_on_company_urn_exact():
    p = _profile(company_urn="urn:li:fsd_company:162479", company_name="Hopper")
    assert _profile_matches_company(p, "urn:li:fsd_company:162479") is True


def test_reverify_drops_ex_employee_wrong_urn():
    # Search-index lag: still indexed under Hopper, but current employer is Google.
    p = _profile(company_urn="urn:li:fsd_company:1441", company_name="Google")
    assert _profile_matches_company(p, "urn:li:fsd_company:162479") is False


def test_reverify_keeps_when_no_position_urn_trusting_the_scope():
    # Privacy-limited profile: no current-position company urn to read. The
    # currentCompany-scoped search already vouched for them, so we KEEP — we must
    # never fabricate a mismatch, and never fall back to loose name matching.
    assert _profile_matches_company(_profile(company_name="Hopper"), "urn:li:fsd_company:162479")
    assert _profile_matches_company(_profile(), "urn:li:fsd_company:162479")


def test_reverify_does_not_name_match_namesake_companies():
    # THE "zip" REGRESSION: a person at "RR ZIP LIMITED" (a different company that
    # merely contains the word) must NOT be kept for target "zip". With no readable
    # position urn we trust the scope (keep); with a DIFFERENT readable urn we drop.
    diff = _profile(company_urn="urn:li:fsd_company:88888", company_name="RR ZIP LIMITED")
    assert _profile_matches_company(diff, "urn:li:fsd_company:162479") is False


def test_reverify_no_target_urn_cannot_filter():
    # Standalone-CLI keyword mode (no entity to scope by) — nothing to verify.
    assert _profile_matches_company(_profile(company_name="Anything"), None) is True
