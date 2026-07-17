# voyager_py/tests/test_voyager_parse.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""The Voyager profile-response parser (forked verbatim). Synthetic fixtures —
no captured LinkedIn payloads (those are large + account-linked). We assert the
graph-resolution + degree extraction the discovery/status paths depend on."""

from __future__ import annotations

from sidecar.packages.referral_outreach.upstream.voyager import (
    parse_connection_degree,
    parse_linkedin_voyager_response,
)

_MEMBER_REL_TYPE = "com.linkedin.voyager.dash.relationships.MemberRelationship"
_PROFILE_TYPE = "com.linkedin.voyager.dash.identity.profile.Profile"


def _response(distance: str | None, *, connected: bool = False) -> dict:
    rel_urn = "urn:li:fsd_memberRelationship:jane"
    union = {"connection": {}} if connected else {"noConnection": {"memberDistance": distance}}
    profile = {
        "entityUrn": "urn:li:fsd_profile:jane",
        "$type": _PROFILE_TYPE,
        "$recipeTypes": ["com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities"],
        "publicIdentifier": "jane-doe",
        "firstName": "Jane",
        "lastName": "Doe",
        "headline": "Staff Engineer at Acme",
        "*memberRelationship": rel_urn,
    }
    rel = {"entityUrn": rel_urn, "$type": _MEMBER_REL_TYPE, "memberRelationshipUnion": union}
    return {"data": {"*elements": ["urn:li:fsd_profile:jane"]}, "included": [profile, rel]}


def test_parse_second_degree_profile():
    out = parse_linkedin_voyager_response(_response("DISTANCE_2"), public_identifier="jane-doe")
    assert out["public_identifier"] == "jane-doe"
    assert out["full_name"] == "Jane Doe"
    assert out["headline"] == "Staff Engineer at Acme"
    assert out["connection_degree"] == 2
    assert out["urn"] == "urn:li:fsd_profile:jane"


def test_parse_first_degree_connection():
    out = parse_linkedin_voyager_response(_response(None, connected=True),
                                          public_identifier="jane-doe")
    assert out["connection_degree"] == 1


def test_parse_connection_degree_scans_included_directly():
    assert parse_connection_degree(_response("DISTANCE_3")) == 3
    assert parse_connection_degree(_response(None, connected=True)) == 1


def _response_unlinked(distance: str) -> dict:
    """A profile whose entity does NOT carry *memberRelationship, but a
    MemberRelationship entity is present in `included` — the exact shape that
    dropped `connection_degree` to NULL on every discovered row."""
    profile = {
        "entityUrn": "urn:li:fsd_profile:jane",
        "$type": _PROFILE_TYPE,
        "$recipeTypes": ["com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities"],
        "publicIdentifier": "jane-doe",
        "firstName": "Jane",
        "lastName": "Doe",
        # NOTE: no "*memberRelationship" link.
    }
    rel = {
        "entityUrn": "urn:li:fsd_memberRelationship:jane",
        "$type": _MEMBER_REL_TYPE,
        "memberRelationshipUnion": {"noConnection": {"memberDistance": distance}},
    }
    return {"data": {"*elements": ["urn:li:fsd_profile:jane"]}, "included": [profile, rel]}


def test_parse_degree_falls_back_to_included_scan_when_unlinked():
    # Regression: the discovery FullProfileWithEntities response often omits the
    # profile→*memberRelationship link; degree must still resolve via the scan.
    out = parse_linkedin_voyager_response(_response_unlinked("DISTANCE_3"),
                                          public_identifier="jane-doe")
    assert out["connection_degree"] == 3
    assert out["connection_distance"] == "DISTANCE_3"


def test_parse_degree_none_when_no_relationship_anywhere():
    profile = {
        "entityUrn": "urn:li:fsd_profile:jane",
        "$type": _PROFILE_TYPE,
        "$recipeTypes": ["com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities"],
        "publicIdentifier": "jane-doe",
        "firstName": "Jane",
        "lastName": "Doe",
    }
    resp = {"data": {"*elements": ["urn:li:fsd_profile:jane"]}, "included": [profile]}
    out = parse_linkedin_voyager_response(resp, public_identifier="jane-doe")
    assert out["connection_degree"] is None


def test_missing_profile_entity_raises():
    import pytest

    with pytest.raises(ValueError):
        parse_linkedin_voyager_response({"data": {}, "included": []})
