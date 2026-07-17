# voyager_py/tests/test_company_resolve.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Company-entity resolution — the pure parsers behind `resolve_company`.

Synthetic typeahead / company-detail fixtures (no captured LinkedIn payloads).
We assert the two shapes we handle, the id/domain helpers, and the
domain-anchor match — the logic the app's silent-auto-pick vs user-confirm
decision rides on (docs/referral-outreach-discovery-design.md §2)."""

from __future__ import annotations

from sidecar.packages.referral_outreach.upstream.company import (
    company_id_from_urn,
    domains_match,
    parse_company_entity,
    parse_company_website,
    parse_typeahead_hits,
    registrable_domain,
    vanity_from_company_url,
)


def test_company_id_from_urn_variants():
    assert company_id_from_urn("urn:li:fsd_company:162479") == "162479"
    assert company_id_from_urn("urn:li:company:99") == "99"
    assert company_id_from_urn("urn:li:fsd_profile:jane") == ""  # not a company
    assert company_id_from_urn(None) == ""
    assert company_id_from_urn("garbage") == ""


def test_registrable_domain_and_match():
    assert registrable_domain("https://www.Abnormal.ai/careers/jobs/1") == "abnormal.ai"
    assert registrable_domain("careers.airbnb.com") == "airbnb.com"
    assert registrable_domain("http://atob.com") == "atob.com"
    assert registrable_domain("") == ""
    assert domains_match("https://abnormal.ai/x", "careers.abnormal.ai") is True
    assert domains_match("atob.com", "goatob.com") is False
    assert domains_match("", "atob.com") is False


def _typeahead_shape_a() -> dict:
    """Classic hitsV2 with hitInfo → TypeaheadCompany."""
    return {
        "elements": [
            {"hitInfo": {"com.linkedin.voyager.typeahead.TypeaheadCompany": {
                "id": 162479, "name": "Hopper", "industry": "Software Development",
                "companyPublicIdentifier": "hopper",
            }}},
            {"hitInfo": {"com.linkedin.voyager.typeahead.TypeaheadCompany": {
                "id": 555, "name": "Hopper Ventilation", "industry": "Facilities Services",
                "navigationUrl": "https://www.linkedin.com/company/hopper-ventilation",
            }}},
            # A non-company hit (person) must be ignored — no company id/vanity.
            {"hitInfo": {"com.linkedin.voyager.typeahead.TypeaheadProfile": {
                "id": "robert", "firstName": "Robert", "lastName": "Hopper",
            }}},
        ]
    }


def _typeahead_shape_b() -> dict:
    """Dash element with targetUrn + title + subtext + navigationUrl."""
    return {
        "elements": [
            {"targetUrn": "urn:li:company:99", "title": {"text": "AtoB"},
             "subtext": {"text": "Financial Services"},
             "navigationUrl": "https://www.linkedin.com/company/goatob?trk=x"},
        ]
    }


def test_parse_typeahead_shape_a_keeps_companies_only():
    hits = parse_typeahead_hits(_typeahead_shape_a())
    assert [h["name"] for h in hits] == ["Hopper", "Hopper Ventilation"]  # person dropped
    assert hits[0]["company_id"] == "162479"
    assert hits[0]["vanity"] == "hopper"
    assert hits[0]["industry"] == "Software Development"
    assert hits[1]["vanity"] == "hopper-ventilation"  # pulled from navigationUrl


def test_parse_typeahead_shape_b_dash():
    hits = parse_typeahead_hits(_typeahead_shape_b())
    assert len(hits) == 1
    assert hits[0]["company_id"] == "99"
    assert hits[0]["name"] == "AtoB"
    assert hits[0]["vanity"] == "goatob"  # slug 'atob' ≠ vanity 'goatob' — the real case
    assert hits[0]["industry"] == "Financial Services"


def test_parse_typeahead_limit_and_dedup():
    payload = {"elements": [
        {"targetUrn": "urn:li:company:1", "title": {"text": "A"}},
        {"targetUrn": "urn:li:company:1", "title": {"text": "A dup"}},  # same id → dropped
        {"targetUrn": "urn:li:company:2", "title": {"text": "B"}},
        {"targetUrn": "urn:li:company:3", "title": {"text": "C"}},
    ]}
    hits = parse_typeahead_hits(payload, limit=2)
    assert [h["company_id"] for h in hits] == ["1", "2"]


def test_parse_typeahead_malformed_never_raises():
    assert parse_typeahead_hits({}) == []
    assert parse_typeahead_hits({"elements": "nope"}) == []
    assert parse_typeahead_hits(None) == []  # type: ignore[arg-type]
    assert parse_typeahead_hits({"elements": [{}, {"hitInfo": {}}]}) == []


def test_vanity_from_company_url():
    assert vanity_from_company_url("https://www.linkedin.com/company/theziphq/") == "theziphq"
    assert vanity_from_company_url("https://linkedin.com/company/goatob?trk=x") == "goatob"
    assert vanity_from_company_url("https://www.linkedin.com/school/mit/") == "mit"
    assert vanity_from_company_url("theziphq") == "theziphq"  # bare vanity accepted
    assert vanity_from_company_url("https://example.com/not-a-company") == ""
    assert vanity_from_company_url("") == ""


def test_parse_company_entity_from_universal_name_response():
    payload = {"elements": [{
        "entityUrn": "urn:li:fs_normalized_company:162479",
        "name": "Zip", "universalName": "theziphq",
        "companyPageUrl": "https://zip.com",
        "companyIndustries": [{"localizedName": "Financial Services"}],
    }]}
    hit = parse_company_entity(payload)
    assert hit is not None
    assert hit["company_id"] == "162479"
    assert hit["name"] == "Zip"
    assert hit["vanity"] == "theziphq"
    assert hit["industry"] == "Financial Services"
    assert hit["website"] == "https://zip.com"


def test_parse_company_entity_none_on_junk():
    assert parse_company_entity({}) is None
    assert parse_company_entity({"elements": [{"name": "no urn"}]}) is None
    assert parse_company_entity(None) is None  # type: ignore[arg-type]


def test_parse_company_website_finds_url_anywhere():
    assert parse_company_website(
        {"elements": [{"name": "AtoB", "companyPageUrl": "https://www.atob.com/"}]}
    ) == "https://www.atob.com/"
    # Nested + fallback key.
    assert parse_company_website(
        {"data": {"company": {"websiteUrl": "http://goatob.com"}}}
    ) == "http://goatob.com"
    assert parse_company_website({}) == ""
    assert parse_company_website(None) == ""  # type: ignore[arg-type]


def test_parse_company_entity_never_names_from_industry():
    """2026-07-12 live bug: the company element carried a company URN but no
    inline name; a blind depth-first walk then grabbed an INDUSTRY entity's
    localizedName ("Software Development") — every confirm-candidate showed the
    industry as its name. The name fallback must only take names from company
    entities, and the industry field must still be enriched."""
    payload = {
        "elements": [
            {
                "entityUrn": "urn:li:fsd_company:1337",
                # no name/localizedName on the primary element
                "companyIndustries": [{"localizedName": "Software Development"}],
            }
        ],
        "included": [
            # An industry entity FIRST — the old walk returned this.
            {"entityUrn": "urn:li:fsd_industry:4", "localizedName": "Software Development"},
            # The actual company entity with the real display name.
            {
                "entityUrn": "urn:li:fsd_company:1337",
                "universalName": "6sense",
                "name": "6sense",
            },
        ],
    }
    hit = parse_company_entity(payload)
    assert hit is not None
    assert hit["name"] == "6sense"
    assert hit["industry"] == "Software Development"


def test_parse_company_entity_industry_never_leaks_when_no_company_name():
    """No company entity carries a name anywhere → the name stays empty (the
    caller humanizes the vanity) rather than borrowing an industry string."""
    payload = {
        "elements": [{"entityUrn": "urn:li:fsd_company:9", "companyIndustries": []}],
        "included": [{"entityUrn": "urn:li:fsd_industry:4", "localizedName": "Banking"}],
    }
    hit = parse_company_entity(payload)
    assert hit is not None
    assert hit["name"] == ""
