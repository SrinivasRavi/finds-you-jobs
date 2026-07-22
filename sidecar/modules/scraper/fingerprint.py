"""Content fingerprinting — cross-posting detection the URL key can't do.

The dedup invariant (canonical URL) can't catch the same role posted on a
company's Greenhouse board *and* on LinkedIn: different hosts, different
canonical URLs, two rows. This module flags those probable cross-listings by
content identity: a 64-bit SimHash (Charikar) over 3-token shingles of the
normalized JD text; two descriptions whose hashes agree on ≥ 92% of bits are
near-certainly the same posting. Technique per career-ops's
`fingerprint-core.mjs` (MIT) — reimplemented here from its documented design
(Python `hashlib` in place of Node `crypto`), no upstream code carried; see
`UPSTREAMS.md`.

Flag, never drop: a cross-listing *annotation* rides `trust_flags` and the
user decides — silently merging risks collapsing genuinely distinct postings
(the exact failure the dedup doc warns about). Zero deps, zero LLM.
"""

from __future__ import annotations

import hashlib
import re

SIMILARITY_THRESHOLD = 0.92
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


def is_probable_cross_listing(a: int, b: int) -> bool:
    return similarity(a, b) >= SIMILARITY_THRESHOLD
