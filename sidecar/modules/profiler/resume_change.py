"""Is a resume edit major? More than 300 characters added, and nothing else counts
(maintainer, 2026-10-13). Deterministic and free, so it runs on every save. Why
300, measured on a real resume, and what a major edit does:
docs/internal/score-storage.md section 5.2."""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

# Above the longest single bullet (298) and below the shortest new job or
# project block (428) on the maintainer's resume.
MAJOR_EDIT_CHARS = 300

_TOKEN = re.compile(r"\S+")


def added_chars(before: str, after: str) -> int:
    """Characters on the new side of a word diff. A line that only moved counts
    nothing, and a fixed typo counts its 1 word, so deleting, reordering and small
    corrections stay well under the bar."""
    old = Counter(line.strip() for line in before.splitlines() if line.strip())
    kept_old: list[str] = []
    new_lines: list[str] = []
    for line in after.splitlines():
        key = line.strip()
        if not key:
            continue
        if old[key] > 0:
            old[key] -= 1  # present before: unchanged or moved
        else:
            new_lines.append(key)
    for line in before.splitlines():
        key = line.strip()
        if key and old[key] > 0:
            old[key] -= 1
            kept_old.append(key)  # gone from the new side: removed or rewritten
    a = _TOKEN.findall("\n".join(kept_old))
    b = _TOKEN.findall("\n".join(new_lines))
    total = 0
    for tag, _i1, _i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            total += len(" ".join(b[j1:j2]))
    return total


def is_major_edit(before: str, after: str) -> bool:
    """True when `after` adds more than MAJOR_EDIT_CHARS characters to `before`."""
    return added_chars(before, after) > MAJOR_EDIT_CHARS
