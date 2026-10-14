"""Canonical-URL normalization — the dedup key (FR-SYS-01, ROADMAP section 4).

Same posting, same key: lowercase scheme/host, default ports and fragments
dropped, tracking params stripped, remaining params sorted, trailing slash
trimmed. Deliberately conservative — `www.` and meaningful query params
(e.g. Greenhouse's `gh_jid`) are kept, because over-normalizing merges
postings that are actually distinct.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tracking/analytics params that never identify a posting.
_TRACKING_PARAMS = {"ref", "referer", "referrer", "src", "source", "medium", "campaign"}
_TRACKING_PREFIXES = ("utm_",)
# ATS-specific noise: Greenhouse source tag, Lever origin tag.
_TRACKING_PARAMS |= {"gh_src", "lever-origin", "lever-source", "lever-source[]"}
_TRACKING_ALSO = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"}
_TRACKING_PARAMS |= _TRACKING_ALSO


# SmartRecruiters serves a posting at `/<company>/<id>`, also as `<id>-<title>`;
# the API-shaped `/<company>/postings/<id>` is a 404 (audit-2026-10-14.md 8.4).
_SMARTRECRUITERS_HOSTS = {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}
_SMARTRECRUITERS_POSTING = re.compile(r"^/([^/]+)/(?:postings/)?(\d+)(?:-[^/]*)?$")


def canonicalize_url(url: str) -> str:
    """Return the canonical form of `url`, or "" if it isn't a usable http(s) URL."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return ""

    host = parts.netloc.lower()
    scheme = parts.scheme.lower()
    if (scheme == "https" and host.endswith(":443")) or (scheme == "http" and host.endswith(":80")):
        host = host.rsplit(":", 1)[0]

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    if host in _SMARTRECRUITERS_HOSTS:
        posting = _SMARTRECRUITERS_POSTING.match(path)
        if posting:
            # The id alone names the posting; its title slug and query are noise.
            return f"https://jobs.smartrecruiters.com/{posting[1]}/{posting[2]}"

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, host, path, query, ""))
