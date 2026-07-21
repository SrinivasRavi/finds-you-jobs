"""Title/location filters — the shared pipeline's user-facing gates.

Word-boundary matching throughout: the career-ops substring lessons ("CTO"
matching inside "Director", #1169; short acronyms, #1101). `always_allow`
exists to rescue multi-location postings ("Remote, New York or India" passes
even with a blocked location present) — career-ops `location_filter` model.

Unknown (empty) location passes the location filter: the scanner can't
confidently exclude what a source didn't state (rank-don't-gate); quality
flags it `no-location` so the user still sees the gap.
"""

from __future__ import annotations

import re

from .types import ScanPrefs


def keyword_match(text: str, keywords: list[str]) -> bool:
    """True if any keyword occurs in `text` as whole words (case-insensitive).

    Multi-word keywords match across any whitespace run.
    """
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        pattern = r"\b" + r"\s+".join(re.escape(part) for part in kw.split()) + r"\b"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def passes_title(title: str, prefs: ScanPrefs) -> bool:
    """Block wins over allow; empty allow-list means everything passes."""
    if keyword_match(title, prefs.title_block):
        return False
    if not prefs.title_allow:
        return True
    return keyword_match(title, prefs.title_allow)


def passes_location(location: str, prefs: ScanPrefs) -> bool:
    """always_allow → pass; block → fail; empty allow or unknown location → pass."""
    if keyword_match(location, prefs.location_always_allow):
        return True
    if keyword_match(location, prefs.location_block):
        return False
    if not prefs.location_allow or not location.strip():
        return True
    return keyword_match(location, prefs.location_allow)


# Curated default sponsorship-denial vocabulary (career-ops `visa_filter`
# model — ships a working default, user list replaces it wholesale).
# Multi-word phrases match across whitespace runs; deliberately conservative:
# only explicit denials, never ambiguous phrasing like "must be authorized to
# work" (which many sponsoring employers also print).
DEFAULT_VISA_PHRASES = [
    "no visa sponsorship",
    "no sponsorship",
    "cannot sponsor",
    "can not sponsor",
    "unable to sponsor",
    "not able to sponsor",
    "will not sponsor",
    "does not sponsor",
    "we do not sponsor",
    "sponsorship is not available",
    "sponsorship not available",
    "without sponsorship now or in the future",
]


def passes_visa(description: str, prefs: ScanPrefs) -> bool:
    """Off by default; when on, an explicit sponsorship denial in the
    description drops the row. Empty description passes (no signal)."""
    if not prefs.visa_filter or not description.strip():
        return True
    return not keyword_match(description, prefs.visa_phrases or DEFAULT_VISA_PHRASES)


def passes_company(company: str, prefs: ScanPrefs) -> bool:
    """Block-only gate. Unknown (empty) company always passes — can't exclude
    what we don't know (rank-don't-gate, same stance as unknown location)."""
    if not company.strip():
        return True
    return not keyword_match(company, prefs.company_block)


def passes_content(title: str, description: str, prefs: ScanPrefs) -> bool:
    """Block wins over allow; empty allow-list means everything passes.
    Empty description always passes — no signal to filter on, not a reason to
    drop a row (rank-don't-gate). Scoped rules (`content_by_title`,
    career-ops's `content_filter.by_title_keyword`) apply the same semantics
    but only to jobs whose title matches the rule's keywords."""
    if not description.strip():
        return True
    if keyword_match(description, prefs.content_block):
        return False
    if prefs.content_allow and not keyword_match(description, prefs.content_allow):
        return False
    for rule in prefs.content_by_title:
        if not keyword_match(title, rule.title):
            continue
        if keyword_match(description, rule.block):
            return False
        if rule.allow and not keyword_match(description, rule.allow):
            return False
    return True
