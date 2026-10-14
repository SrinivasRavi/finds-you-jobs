"""Parse the skill's output contract: ===SCORE=== int, ===REASONS=== bullets,
===BREAKDOWN=== markdown, and the optional ===FACTORS=== lines."""

from __future__ import annotations

import re

from .types import Factor, ScoreError

_SCORE_RE = re.compile(r"===\s*SCORE\s*===\s*\n(.*?)\n===\s*REASONS\s*===", re.DOTALL)
_REASONS_RE = re.compile(r"===\s*REASONS\s*===\s*\n(.*?)\n===\s*BREAKDOWN\s*===", re.DOTALL)
# \s* (not \s*\n) so a trailing-whitespace-only block still matches and hits
# the dedicated "empty" error below instead of the generic contract error.
# The breakdown stops at FACTORS when that block is present and runs to the end
# when it is not, because the skill file is user-editable: an override that
# predates FACTORS has to keep parsing.
_BREAKDOWN_RE = re.compile(r"===\s*BREAKDOWN\s*===\s*(.*?)(?:\n===\s*FACTORS\s*===|\Z)", re.DOTALL)
_FACTORS_RE = re.compile(r"===\s*FACTORS\s*===\s*(.*)\Z", re.DOTALL)
_FACTOR_LINE_RE = re.compile(r"^[-*\s]*([a-z_]{3,40})\s*:\s*([0-9]*\.?[0-9]+)\s*(?:\|(.*))?$")

# The axes the product draws and weights. A key outside this set is dropped
# rather than stored: the bar chart, the weights and the cross-method comparison
# all read a fixed vocabulary, so a model inventing an 8th axis must not reach
# the database (`scoring.md` section 7.2).
FACTOR_KEYS: tuple[str, ...] = (
    "hard_requirements",
    "core_stack",
    "years",
    "level",
    "location_preference",
    "logistics",
    "nice_to_haves",
)


def parse_factors(block: str) -> list[Factor]:
    """`key: value | evidence` lines into typed factors; junk lines are skipped.

    Tolerant on purpose. The factors are an addition to a contract whose score
    and prose already parsed, so a malformed line costs a bar, never the call.
    """
    factors: list[Factor] = []
    seen: set[str] = set()
    for line in block.strip().splitlines():
        match = _FACTOR_LINE_RE.match(line.strip())
        if match is None:
            continue
        key = match.group(1)
        if key not in FACTOR_KEYS or key in seen:
            continue
        try:
            value = float(match.group(2))
        except ValueError:
            continue
        if not 0.0 <= value <= 1.0:
            continue
        seen.add(key)
        factors.append(Factor(key, value, evidence=(match.group(3) or "").strip()))
    return sorted(factors, key=lambda f: FACTOR_KEYS.index(f.factor))


def parse_output(raw: str) -> tuple[int, list[str], str, list[Factor]]:
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

    m_factors = _FACTORS_RE.search(stripped)
    factors = parse_factors(m_factors.group(1)) if m_factors else []

    return score, reasons, breakdown_md, factors
