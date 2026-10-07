"""The dedup key for a job with no posting URL (url-less-jobs.md section 5.2).

A scanned job is keyed on its canonical URL. A job the user typed in has no
URL to be keyed on, so it is keyed on its description: SHA-256 over a
normalized token stream, which makes a re-paste of the same text collide with
the first paste by construction instead of writing a second row.

Normalization is NFKC, lowercase, `[a-z0-9]+` tokens joined by single spaces.
Stop words stay in, and there is no length cap — both measured on 112 real
postings in section 6.2. Tokenizing found a group of near-identical postings
that raw hashing missed (3 OneTrust requisitions differing by 1 non-breaking
space in 7,437 characters); dropping stop words found nothing extra and would
make 2 genuinely different JDs collide.

This answers "is this the same text?" exactly. "Is this nearly the same text?"
is `fingerprint.simhash64`, which rides beside it.
"""

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
