# voyager_py/tests/test_discovery_filter.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Discovery's URN-scoped search URL + the enrich-time re-verify filter — the
L1/L2 correctness core (docs/referral-outreach-discovery-design.md section 2). Pure;
no browser, no network."""

from __future__ import annotations

from typing import Any, cast
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


def test_discover_runs_the_search_and_each_candidate_as_its_own_step(monkeypatch):
    """The whole walk used to be 1 lane action: 6 to 10 s pauses between up to 10
    profiles outran its 45 s deadline, so discover failed for any company with more
    than about 4 candidates (2026-10-10 live, 46.0 s). Now 1 step for the search, 1
    per candidate, and every pause between them runs outside a step."""
    from sidecar.packages.referral_outreach.upstream import discovery

    urls = [f"https://www.linkedin.com/in/person-{i}" for i in range(4)]
    events: list[str] = []
    monkeypatch.setattr(discovery, "search_candidates", lambda *a, **k: urls)

    def enrich(_session, url, public_id, _urn):
        events.append(f"enrich:{public_id}")
        degree = 2 if public_id.endswith("1") else 1
        if public_id.endswith("3"):
            return None  # moved employer: dropped, still paced
        return {"public_identifier": public_id, "url": url, "connection_degree": degree}

    monkeypatch.setattr(discovery, "enrich_candidate", enrich)

    def run(step):
        events.append("step")
        return step()

    contacts = discovery.discover_company_contacts(
        cast(Any, object()), "Acme", limit=10, run=run, pause=lambda: events.append("pause")
    )
    assert events == [
        "step",
        "step", "enrich:person-0",
        "pause", "step", "enrich:person-1",
        "pause", "step", "enrich:person-2",
        "pause", "step", "enrich:person-3",
    ]
    assert [c["public_identifier"] for c in contacts] == ["person-0", "person-2", "person-1"]


def test_discover_stops_enriching_at_the_limit(monkeypatch):
    from sidecar.packages.referral_outreach.upstream import discovery

    urls = [f"https://www.linkedin.com/in/p-{i}" for i in range(6)]
    monkeypatch.setattr(discovery, "search_candidates", lambda *a, **k: urls)
    monkeypatch.setattr(
        discovery,
        "enrich_candidate",
        lambda _s, url, pid, _u: {"public_identifier": pid, "url": url, "connection_degree": 1},
    )
    steps: list[int] = []
    contacts = discovery.discover_company_contacts(
        cast(Any, object()),
        "Acme",
        limit=2,
        run=lambda f: (steps.append(1), f())[1],
        pause=lambda: None,
    )
    assert len(contacts) == 2
    assert len(steps) == 3  # the search, then exactly 2 candidates
