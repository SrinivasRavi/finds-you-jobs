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


def passes_company(company: str, prefs: ScanPrefs) -> bool:
    """Block-only gate. Unknown (empty) company always passes — can't exclude
    what we don't know (rank-don't-gate, same stance as unknown location)."""
    if not company.strip():
        return True
    return not keyword_match(company, prefs.company_block)


def passes_content(description: str, prefs: ScanPrefs) -> bool:
    """Block wins over allow; empty allow-list means everything passes.
    Empty description always passes — no signal to filter on, not a reason to
    drop a row (rank-don't-gate)."""
    if not description.strip():
        return True
    if keyword_match(description, prefs.content_block):
        return False
    if not prefs.content_allow:
        return True
    return keyword_match(description, prefs.content_allow)
