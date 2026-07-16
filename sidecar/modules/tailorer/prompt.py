"""Assemble the single-operation prompt: skill + master + JD + guidance + samples.

The skill file (tailor-resume-skill.md, distilled from career-ops) is the system
prompt; the per-run inputs arrive as clearly delimited blocks. Everything is
in-context — the operation has no tools and no file access (ROADMAP §4).
"""

from __future__ import annotations

from pathlib import Path

from sidecar.modules._shared.skill_md import load_skill_md
from sidecar.modules._shared.writing_samples import (
    load_writing_samples as _shared_load_writing_samples,
)

SKILL_PATH = Path(__file__).parent / "tailor-resume-skill.md"


def load_skill() -> str:
    # Comment stripping (staged-but-disabled rules stay authoring-side) lives in
    # _shared/skill_md.py — extracted at the second consumer, the Scorer.
    return load_skill_md(SKILL_PATH)


def build_user_prompt(
    master_md: str,
    jd_md: str,
    guidance: str = "",
    writing_samples: list[tuple[str, str]] | None = None,
) -> str:
    parts = [
        "=== MASTER RESUME (source of truth) ===",
        master_md.strip(),
        "",
        "=== JOB DESCRIPTION ===",
        jd_md.strip(),
    ]
    if guidance.strip():
        parts += ["", "=== PER-JOB GUIDANCE (from the user) ===", guidance.strip()]
    for name, text in writing_samples or []:
        parts += ["", f"=== WRITING SAMPLE: {name} (style calibration only) ===", text.strip()]
    parts += [
        "",
        "Produce the tailored resume now, following the skill instructions and the",
        "output contract exactly (===RESUME=== then ===NOTES===).",
    ]
    return "\n".join(parts)


# Mechanics live in _shared/writing_samples.py — extracted at the second
# consumer, the CoverLetterer; re-exported here to keep the tailorer API stable.
load_writing_samples = _shared_load_writing_samples
