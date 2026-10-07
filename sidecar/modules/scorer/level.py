"""Deterministic seniority-level extractor for job titles.

Measured 2026-10-01 over the 93 distinct titles on the maintainer's install
(docs/internal/evidence/2026-10-01-location-resolution/README.md, "Titles"):
an 11-rung word ladder labels 70 of 93. Word tokens (Senior, Staff, Principal,
...) and standalone roman-numeral/digit tokens (Engineer II) both carry a
level; when a title has both, the word wins, because a company that writes
"Senior ... II" means a senior engineer on its own ladder. Among word matches,
the longest matched text wins, then the higher level, which is why "Senior
Staff" reads as `SENIOR_STAFF` and not `SENIOR`. No I/O, no model, pure regex.
"""

from __future__ import annotations

import re
from enum import StrEnum


class Level(StrEnum):
    INTERN = "intern"
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    SENIOR_STAFF = "senior_staff"
    PRINCIPAL = "principal"
    LEAD = "lead"
    MANAGER = "manager"
    DIRECTOR = "director"
    VP = "vp"


LEVEL_ORDER: tuple[Level, ...] = (
    Level.INTERN,
    Level.ENTRY,
    Level.MID,
    Level.SENIOR,
    Level.STAFF,
    Level.SENIOR_STAFF,
    Level.PRINCIPAL,
    Level.LEAD,
    Level.MANAGER,
    Level.DIRECTOR,
    Level.VP,
)

_RANK = {level: i for i, level in enumerate(LEVEL_ORDER)}

# Word-level tokens, wrapped with alnum lookaround instead of \b so an optional
# trailing period ("Jr.", "Sr.") still ends the match at end-of-string or
# before a space, where \b would fail (both sides non-word, no boundary).
_RAW_WORD_PATTERNS: tuple[tuple[str, Level], ...] = (
    (r"senior\s+staff", Level.SENIOR_STAFF),
    (r"sr\.?\s+staff", Level.SENIOR_STAFF),
    (r"vice\s+president", Level.VP),
    (r"new\s+grad", Level.ENTRY),
    (r"entry[\s-]level", Level.ENTRY),
    (r"intern(?:ship)?", Level.INTERN),
    (r"associate", Level.ENTRY),
    (r"jr\.?", Level.ENTRY),
    (r"junior", Level.ENTRY),
    (r"graduate", Level.ENTRY),
    (r"trainee", Level.ENTRY),
    (r"apprentice", Level.ENTRY),
    (r"architect", Level.PRINCIPAL),
    (r"distinguished", Level.PRINCIPAL),
    (r"principal", Level.PRINCIPAL),
    (r"director", Level.DIRECTOR),
    (r"vp", Level.VP),
    (r"staff", Level.STAFF),
    (r"sr\.?", Level.SENIOR),
    (r"senior", Level.SENIOR),
)

_WORD_PATTERNS: tuple[tuple[re.Pattern[str], Level], ...] = tuple(
    (re.compile(rf"(?<![a-z0-9]){raw}(?![a-z0-9])"), level) for raw, level in _RAW_WORD_PATTERNS
)

# The management words sit outside the main pool, because a title that states an
# IC rung states its level there: "Staff Software Engineer, Tech Lead" is a Staff
# posting, and "Staff ... Password Manager (PWM)" is not a manager posting at all.
# These fire only when nothing else, word or numeral, said anything, which is
# what leaves "Engineering Lead" and "Head of Platform" readable.
_FALLBACK_RAW: tuple[tuple[str, Level], ...] = (
    (r"tech\s+lead", Level.LEAD),
    (r"team\s+lead", Level.LEAD),
    (r"head\s+of", Level.MANAGER),
    (r"lead", Level.LEAD),
    (r"manager", Level.MANAGER),
)

_FALLBACK_WORD_PATTERNS: tuple[tuple[re.Pattern[str], Level], ...] = tuple(
    (re.compile(rf"(?<![a-z0-9]){raw}(?![a-z0-9])"), level) for raw, level in _FALLBACK_RAW
)

# A standalone roman numeral or 1-2 digit token, isolated on both sides by
# non-alnum (or the string edge), so it never matches inside "AI" or "L4".
_NUMERAL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([IVXivx]{1,3}|\d{1,2})(?![A-Za-z0-9])")
_NUMERAL_LEVELS: dict[str, Level] = {
    "i": Level.ENTRY,
    "1": Level.ENTRY,
    "ii": Level.MID,
    "2": Level.MID,
    "iii": Level.SENIOR,
    "3": Level.SENIOR,
}
_TRAILING_SEPARATOR_RE = re.compile(r"\s*(?:[,\-(/]|$)")
_ADJACENT_WORD_RE = re.compile(r"[A-Za-z]+")


def _is_shouting(word: str) -> bool:
    return len(word) >= 2 and word.isupper()


def _is_better(candidate: tuple[Level, int], best: tuple[Level, int] | None) -> bool:
    """Longest span wins; a tie goes to the higher level (rule: "longest match
    wins, then highest level wins")."""
    if best is None:
        return True
    if candidate[1] != best[1]:
        return candidate[1] > best[1]
    return _RANK[candidate[0]] > _RANK[best[0]]


def _best_word_match(
    lowered: str, patterns: tuple[tuple[re.Pattern[str], Level], ...]
) -> Level | None:
    best: tuple[Level, int] | None = None
    for pattern, level in patterns:
        match = pattern.search(lowered)
        if match is None:
            continue
        candidate = (level, match.end() - match.start())
        if _is_better(candidate, best):
            best = candidate
    return best[0] if best is not None else None


def _numeral_level(title: str) -> Level | None:
    """A roman numeral or digit counts as a level only when it stands alone
    before a separator or the title's end, and neither neighboring word is a
    shouted abbreviation (`IBM i` must never read as `i` the entry-level `I`:
    `IBM` is the giveaway, not the title's own grammar)."""
    best: tuple[Level, int] | None = None
    for match in _NUMERAL_TOKEN_RE.finditer(title):
        level = _NUMERAL_LEVELS.get(match.group(1).lower())
        if level is None:
            continue
        if not _TRAILING_SEPARATOR_RE.match(title, match.end()):
            continue
        before = _ADJACENT_WORD_RE.findall(title[: match.start()])
        after = _ADJACENT_WORD_RE.findall(title[match.end() :])
        if (before and _is_shouting(before[-1])) or (after and _is_shouting(after[0])):
            continue
        candidate = (level, match.end() - match.start())
        if _is_better(candidate, best):
            best = candidate
    return best[0] if best is not None else None


def extract_level(title: str) -> Level | None:
    """The seniority level a posting title states, or `None` when it states
    none. Word tokens always beat a bare numeral token when both appear."""
    if not title:
        return None
    lowered = title.lower()
    word_level = _best_word_match(lowered, _WORD_PATTERNS)
    if word_level is not None:
        return word_level
    numeral_level = _numeral_level(title)
    if numeral_level is not None:
        return numeral_level
    return _best_word_match(lowered, _FALLBACK_WORD_PATTERNS)
