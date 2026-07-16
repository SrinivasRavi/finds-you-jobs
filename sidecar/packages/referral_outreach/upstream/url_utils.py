# voyager_py/url_utils.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# Forked verbatim from OpenOutreach `linkedin/url_utils.py` @ a7a9101.
"""LinkedIn public-identifier ↔ URL helpers (pure, no I/O)."""

from __future__ import annotations

from urllib.parse import quote, unquote, urlparse


def url_to_public_id(url: str) -> str | None:
    """
    Strict LinkedIn public ID extractor:
    - Path MUST start with /in/
    - Returns the second segment, percent-decoded
    - Returns None for empty or non-profile URLs
    """
    if not url:
        return None

    path = urlparse(url.strip()).path
    parts = path.strip("/").split("/")

    if len(parts) < 2 or parts[0] != "in":
        return None

    public_id = parts[1]
    return unquote(public_id)


def public_id_to_url(public_id: str) -> str:
    """Convert public_identifier back to a clean LinkedIn profile URL."""
    if not public_id:
        return ""
    public_id = public_id.strip("/")
    return f"https://www.linkedin.com/in/{quote(public_id, safe='')}/"
