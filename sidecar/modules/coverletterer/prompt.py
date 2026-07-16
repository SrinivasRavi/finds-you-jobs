"""Assemble the single-operation prompt: skill + master + JD + guidance + samples.

The skill file (cover-letter-skill.md, distilled from career-ops's cover mode)
is the system prompt; the per-run inputs arrive as clearly delimited blocks.
Everything is in-context — the operation has no tools and no file access
(ROADMAP §4).
"""

from __future__ import annotations

from pathlib import Path

from sidecar.modules._shared.skill_md import load_skill_md
from sidecar.modules._shared.writing_samples import load_writing_samples

__all__ = ["SKILL_PATH", "build_user_prompt", "load_skill", "load_writing_samples"]

SKILL_PATH = Path(__file__).parent / "cover-letter-skill.md"


def load_skill() -> str:
    return load_skill_md(SKILL_PATH)


def build_user_prompt(
    master_md: str,
    jd_md: str,
    guidance: str = "",
    writing_samples: list[tuple[str, str]] | None = None,
) -> str:
    parts = [
        "=== MASTER RESUME (sole candidate evidence) ===",
        master_md.strip(),
        "",
        "=== JOB DESCRIPTION (sole company facts) ===",
        jd_md.strip(),
    ]
    if guidance.strip():
        parts += ["", "=== PER-JOB GUIDANCE (from the user) ===", guidance.strip()]
    for name, text in writing_samples or []:
        parts += ["", f"=== WRITING SAMPLE: {name} (style calibration only) ===", text.strip()]
    parts += [
        "",
        "Produce the cover letter now, following the skill instructions and the",
        "output contract exactly (===COVER_LETTER=== then ===NOTES===).",
    ]
    return "\n".join(parts)
