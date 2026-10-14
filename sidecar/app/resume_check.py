"""Does an upload look like a resume? (backlog S-C57)

Every score and tailored resume grounds on the master resume, so a wrong file
(a cover letter, a lease, a blank page) or a hostile one poisons all of them.
No LLM: 3 structure signals, and phrases that address an AI. The result is a
warning the user can read and continue past ("rank, don't gate").
"""

from __future__ import annotations

import re

_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
# Section headings, in the 13 languages the app ships.
_SECTION = re.compile(
    r"\b(experience|education|skills|employment|work history|projects|summary|"
    r"berufserfahrung|ausbildung|kenntnisse|expérience|formation|compétences|"
    r"experiencia|educación|habilidades|esperienza|istruzione|competenze|"
    r"experiência|educação|ervaring|opleiding|vaardigheden|doświadczenie|"
    r"wykształcenie|umiejętności|опыт|образование|навыки)\b|"
    r"経歴|学歴|スキル|工作经历|职业经历|教育|技能|خبرة|التعليم|المهارات|अनुभव|शिक्षा|कौशल",
    re.I,
)
_INSTRUCTIONS = re.compile(
    r"ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(above|previous)|"
    r"you are (an? )?(ai|language model|assistant)|system prompt|"
    r"(rate|score) (this|me|the) (candidate|resume) (as )?(10|100|a perfect)",
    re.I,
)
_MIN_CHARS = 400


def resume_warnings(text: str) -> list[str]:
    """`not_a_resume` when at most 1 of the 3 signals is present, and
    `instructions` when the text addresses an AI. Empty means it looks fine."""
    signals = (
        len(text.strip()) >= _MIN_CHARS,
        len(set(_YEAR.findall(text))) >= 2,
        _SECTION.search(text) is not None,
    )
    warnings = []
    if sum(signals) <= 1:
        warnings.append("not_a_resume")
    if _INSTRUCTIONS.search(text):
        warnings.append("instructions")
    return warnings
