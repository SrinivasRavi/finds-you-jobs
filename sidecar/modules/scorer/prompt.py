"""Assemble the single-operation prompt: skill + master + JD.

The skill file (score-job-skill.md, distilled from career-ops's oferta mode) is
the system prompt; the per-run inputs arrive as clearly delimited blocks.
Everything is in-context — the operation has no tools and no file access
(ROADMAP section 4).
"""

from __future__ import annotations

from pathlib import Path

from sidecar.modules._shared.skill_md import load_skill_md

SKILL_PATH = Path(__file__).parent / "score-job-skill.md"


def load_skill() -> str:
    return load_skill_md(SKILL_PATH)


def build_user_prompt(master_md: str, jd_md: str, preferences_md: str | None = None) -> str:
    """The 3 input blocks in the order the skill file reads them: resume (evidence),
    posting (requirements), then optional preferences (intent)."""
    blocks = [
        "=== MASTER RESUME (sole candidate evidence) ===",
        master_md.strip(),
        "",
        "=== JOB DESCRIPTION (sole requirements source) ===",
        jd_md.strip(),
        "",
    ]
    if preferences_md and preferences_md.strip():
        blocks += [
            "=== SEARCH PREFERENCES (the user's stated intent, authoritative over the resume) ===",
            preferences_md.strip(),
            "",
        ]
    return "\n".join(
        [
            *blocks,
            "Produce the fit score now, following the skill instructions and the output",
            "contract exactly (===SCORE=== then ===REASONS=== then ===BREAKDOWN===).",
        ]
    )
