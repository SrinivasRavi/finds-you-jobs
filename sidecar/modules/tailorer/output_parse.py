"""Parse the skill's output contract: ===RESUME=== body, ===NOTES=== bullets."""

from __future__ import annotations

import re

from .types import TailorError

_RESUME_RE = re.compile(r"===\s*RESUME\s*===\s*\n(.*?)\n===\s*NOTES\s*===", re.DOTALL)
_NOTES_RE = re.compile(r"===\s*NOTES\s*===\s*\n(.*)\Z", re.DOTALL)


def parse_output(raw: str) -> tuple[str, list[str]]:
    # Models sometimes wrap the whole contract in a markdown fence; strip one if present.
    stripped = raw.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)

    m_resume = _RESUME_RE.search(stripped)
    m_notes = _NOTES_RE.search(stripped)
    if not m_resume or not m_notes:
        raise TailorError(
            "parse",
            "output did not follow the ===RESUME===/===NOTES=== contract; "
            f"got {len(raw)} chars starting: {raw.strip()[:200]!r}",
        )
    resume_md = m_resume.group(1).strip()
    if not resume_md:
        raise TailorError("parse", "RESUME block is empty")
    notes = [
        line.lstrip("-• ").strip()
        for line in m_notes.group(1).strip().splitlines()
        if line.strip() and line.strip() not in {"-", "•"}
    ]
    return resume_md, notes
