"""Content fingerprinting — cross-posting detection the URL key can't do.

The dedup invariant (canonical URL) can't catch the same role posted on a
company's Greenhouse board *and* on LinkedIn: different hosts, different
canonical URLs, two rows. This module flags those probable cross-listings by
content identity: a 64-bit SimHash (Charikar) over 3-token shingles of the
normalized JD text; two descriptions whose hashes agree on ≥ 90% of bits are
candidates for the same posting, confirmed by company and title. Technique per
career-ops's `fingerprint-core.mjs` (MIT) — reimplemented from its design
(Python `hashlib` in place of Node `crypto`), no upstream code carried; see
`UPSTREAMS.md`.

Flag, never drop: a cross-listing *annotation* rides `trust_flags` and the
user decides — silently merging risks collapsing genuinely distinct postings
(the exact failure the dedup doc warns about). Zero deps, zero LLM.
"""

from __future__ import annotations

import hashlib
import re

# 0.90 with the company+title gate below. Similarity alone is 57% precise at
# 0.92 on the 112-posting bench corpus, because employer boilerplate puts any
# 2 postings from 1 company on a high floor (url-less-jobs.md section 6.4).
SIMILARITY_THRESHOLD = 0.90
_SHINGLE = 3
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def simhash64(text: str) -> int:
    """64-bit SimHash over 3-token shingles of `text`; 0 when the text is too
    short to shingle (< 3 tokens) — callers treat 0 as "no fingerprint"."""
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < _SHINGLE:
        return 0
    counts = [0] * 64
    for i in range(len(tokens) - _SHINGLE + 1):
        shingle = " ".join(tokens[i : i + _SHINGLE])
        h = int.from_bytes(
            hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for bit in range(64):
            counts[bit] += 1 if (h >> bit) & 1 else -1
    result = 0
    for bit in range(64):
        if counts[bit] > 0:
            result |= 1 << bit
    return result


def similarity(a: int, b: int) -> float:
    """Fraction of agreeing bits between two fingerprints; 0.0 when either
    is the "no fingerprint" sentinel."""
    if not a or not b:
        return 0.0
    return 1.0 - ((a ^ b).bit_count() / 64)


def normalize_label(value: str) -> str:
    """Company/title normalized for equality: lowercase alphanumeric tokens."""
    return " ".join(_TOKEN_RE.findall((value or "").lower()))


def is_probable_cross_listing(
    a: int,
    b: int,
    *,
    company_a: str = "",
    company_b: str = "",
    title_a: str = "",
    title_b: str = "",
) -> bool:
    """Same company, same title, and descriptions at or above the threshold.

    The 3 parts are one gate: 0 clear false positives on the bench corpus,
    against 9 for the bare similarity test it replaces (section 6.4)."""
    if similarity(a, b) < SIMILARITY_THRESHOLD:
        return False
    if normalize_label(company_a) != normalize_label(company_b):
        return False
    return normalize_label(title_a) == normalize_label(title_b)
