"""Skill-file loader shared by module silos (extracted at the second consumer,
per the M1 playbook — Tailorer and Scorer both load `<name>-skill.md` files).

HTML comments in skill files are authoring-side only (provenance headers,
staged-but-disabled rules like the G7 strict-ordering block) — the model must
never see them, so they are stripped here.
"""

from __future__ import annotations

import re
from pathlib import Path

_HTML_COMMENT = re.compile(r"<!--.*?-->\n?", re.DOTALL)


def load_skill_md(path: Path) -> str:
    return _HTML_COMMENT.sub("", path.read_text())
