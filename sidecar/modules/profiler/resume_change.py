"""Is a resume edit major? A new section or a new entry (job, project, degree) is;
rewording, reordering, formatting and edited bullets are not (maintainer,
2026-10-13). Deterministic and free, so it runs on every save. The alternatives
and why this one: docs/internal/score-storage.md section 5.2."""

from __future__ import annotations

import re

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
# An entry title: a line (or list item) that opens with bold text, the shape a
# job, project or degree takes in a markdown resume ("**Acme** | 2021").
_BOLD_LEAD = re.compile(r"^\s*(?:[-*+]\s+)?\*\*(.+?)\*\*")
_WORD = re.compile(r"[a-z0-9]+")


def _key(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def _titles(markdown: str) -> set[str]:
    """Every section heading and entry title, normalised so case, punctuation and
    spacing changes don't count as new."""
    out: set[str] = set()
    for line in markdown.splitlines():
        m = _HEADING.match(line) or _BOLD_LEAD.match(line)
        if m is None:
            stripped = line.strip()
            # A plain-text resume's headings: a short line in capitals ("EXPERIENCE").
            words = stripped.split()
            if not (0 < len(words) <= 5 and stripped.isupper()):
                continue
            title = stripped
        else:
            title = m.group(1)
        key = _key(title)
        if key:
            out.add(key)
    return out


def is_major_edit(before: str, after: str) -> bool:
    """True when `after` adds a section heading or an entry title `before` lacked."""
    return bool(_titles(after) - _titles(before))
