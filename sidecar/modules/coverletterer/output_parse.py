"""Parse the skill's output contract: ===COVER_LETTER=== body, ===NOTES=== bullets.

A COVER_LETTER block starting with `REFUSED:` (the skill's JD-gate outcome) is
surfaced as a typed error — the module never half-succeeds with a placeholder
letter (vision non-negotiable; upstream's "do not generate a generic letter
under any circumstances").
"""

from __future__ import annotations

import re

from .types import CoverError

_LETTER_RE = re.compile(r"===\s*COVER_LETTER\s*===\s*\n(.*?)\n===\s*NOTES\s*===", re.DOTALL)
_NOTES_RE = re.compile(r"===\s*NOTES\s*===\s*\n(.*)\Z", re.DOTALL)


def parse_output(raw: str) -> tuple[str, list[str]]:
    # Models sometimes wrap the whole contract in a markdown fence; strip one if present.
    stripped = raw.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)

    m_letter = _LETTER_RE.search(stripped)
    m_notes = _NOTES_RE.search(stripped)
    if not m_letter or not m_notes:
        raise CoverError(
            "parse",
            "output did not follow the ===COVER_LETTER===/===NOTES=== contract; "
            f"got {len(raw)} chars starting: {raw.strip()[:200]!r}",
        )
    cover_letter_md = m_letter.group(1).strip()
    if not cover_letter_md:
        raise CoverError("parse", "COVER_LETTER block is empty")
    if cover_letter_md.startswith("REFUSED:"):
        raise CoverError("jd-gate", cover_letter_md.removeprefix("REFUSED:").strip())
    notes = [
        line.lstrip("-• ").strip()
        for line in m_notes.group(1).strip().splitlines()
        if line.strip() and line.strip() not in {"-", "•"}
    ]
    return cover_letter_md, notes
