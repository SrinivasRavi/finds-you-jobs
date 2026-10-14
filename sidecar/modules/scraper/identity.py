"""The dedup key for a job with no posting URL (url-less-jobs.md section 5.2).

SHA-256 over a normalized token stream (NFKC, lowercase, `[a-z0-9]+` tokens), so
a re-paste collides with the first paste. Stop words stay in and there's no
length cap, both measured on 112 postings (section 6.2). This answers "same
text?"; "nearly the same?" is `fingerprint.simhash64`."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    """NFKC, lowercase, alphanumeric tokens joined by single spaces."""
    folded = unicodedata.normalize("NFKC", text or "").lower()
    return " ".join(_TOKEN_RE.findall(folded))


def content_hash(description: str) -> str:
    """The URL-less identity for `description`, or "" when there's no text.

    "" is the caller's signal that this job has no content key either, which
    `POST /api/jobs` refuses rather than writing a row nothing can dedup."""
    normalized = normalize_text(description)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
