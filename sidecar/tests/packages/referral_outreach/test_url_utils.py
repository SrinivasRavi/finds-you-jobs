# voyager_py/tests/test_url_utils.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Public-id ↔ URL helpers (forked verbatim from upstream)."""

from __future__ import annotations

from sidecar.packages.referral_outreach.upstream.url_utils import public_id_to_url, url_to_public_id


def test_extracts_public_id_from_profile_url():
    assert url_to_public_id("https://www.linkedin.com/in/jane-doe/") == "jane-doe"
    assert url_to_public_id("https://www.linkedin.com/in/jane-doe") == "jane-doe"


def test_percent_decodes_public_id():
    assert url_to_public_id("https://www.linkedin.com/in/jos%C3%A9/") == "josé"


def test_non_profile_urls_return_none():
    assert url_to_public_id("https://www.linkedin.com/company/acme/") is None
    assert url_to_public_id("https://www.linkedin.com/feed/") is None
    assert url_to_public_id("") is None


def test_public_id_to_url_roundtrip():
    assert public_id_to_url("jane-doe") == "https://www.linkedin.com/in/jane-doe/"
    assert public_id_to_url("") == ""
    # round-trips through extraction
    assert url_to_public_id(public_id_to_url("jane-doe")) == "jane-doe"
