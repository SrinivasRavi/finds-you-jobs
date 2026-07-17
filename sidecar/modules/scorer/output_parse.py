"""Parse the skill's output contract: ===SCORE=== int, ===REASONS=== bullets,
===BREAKDOWN=== markdown."""

from __future__ import annotations

import re

from .types import ScoreError

_SCORE_RE = re.compile(r"===\s*SCORE\s*===\s*\n(.*?)\n===\s*REASONS\s*===", re.DOTALL)
_REASONS_RE = re.compile(r"===\s*REASONS\s*===\s*\n(.*?)\n===\s*BREAKDOWN\s*===", re.DOTALL)
# \s* (not \s*\n) so a trailing-whitespace-only block still matches and hits
# the dedicated "empty" error below instead of the generic contract error.
_BREAKDOWN_RE = re.compile(r"===\s*BREAKDOWN\s*===\s*(.*)\Z", re.DOTALL)


def parse_output(raw: str) -> tuple[int, list[str], str]:
    # Models sometimes wrap the whole contract in a markdown fence; strip one if present.
    stripped = raw.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)

    m_score = _SCORE_RE.search(stripped)
    m_reasons = _REASONS_RE.search(stripped)
    m_breakdown = _BREAKDOWN_RE.search(stripped)
    if not m_score or not m_reasons or not m_breakdown:
        raise ScoreError(
            "parse",
            "output did not follow the ===SCORE===/===REASONS===/===BREAKDOWN=== contract; "
            f"got {len(raw)} chars starting: {raw.strip()[:200]!r}",
        )

    score_text = m_score.group(1).strip()
    score_match = re.fullmatch(r"(\d{1,3})(?:\s*/\s*100)?", score_text)
    if not score_match:
        raise ScoreError("parse", f"SCORE block is not an integer: {score_text!r}")
    score = int(score_match.group(1))
    if not 0 <= score <= 100:
        raise ScoreError("parse", f"score {score} is outside 0–100")

    reasons = [
        line.lstrip("-• ").strip()
        for line in m_reasons.group(1).strip().splitlines()
        if line.strip() and line.strip() not in {"-", "•"}
    ]
    # US-JB-05 wants 2–4 bullets. Too few is a real contract failure (no
    # signal to show); too MANY is the model being chatty — failing the whole
    # op (and eating the spend) over that was overzealous (2026-07-17
    # dogfood: "got 5" burned a $0.30 call). Keep the strongest cut: the
    # model orders reasons by importance, so truncate to the first 4.
    if len(reasons) < 2:
        raise ScoreError(
            "parse", f"REASONS must be 2–4 bullets (US-JB-05); got {len(reasons)}"
        )
    reasons = reasons[:4]

    breakdown_md = m_breakdown.group(1).strip()
    if not breakdown_md:
        raise ScoreError("parse", "BREAKDOWN block is empty")

    return score, reasons, breakdown_md
