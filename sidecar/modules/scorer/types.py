"""Scorer module types — plain dataclasses, pre-architecture (ROADMAP section 4).

No pydantic yet: the module is a silo; the G4 architecture pass decides the
final type system and these graduate into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Usage:
    """Aggregate cost record for one bounded operation (ROADMAP section 4).

    Recorded always; NOT enforced as a budget pre-beta (maintainer decision,
    docs/ROADMAP.md section 4 — enforcement is a pre-beta work item).
    """

    internal_calls: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    usd: float | None = None
    latency_ms: int | None = None
    model: str | None = None


@dataclass(frozen=True)
class Factor:
    """1 axis of a score, in the shape every method writes (scoring.md 7.2). `value`
    is 0 to 1; `kind` is `graded` (a weight may multiply it) or `probability` (it
    may not). An unassessed factor is absent, never a middle value."""

    factor: str
    value: float
    kind: str = "graded"
    evidence: str = ""


@dataclass
class ScoreResult:
    """Output of one score() operation.

    `breakdown_md` is the same-pass structured output behind the reasons —
    US-JB-05's P2 per-criterion display reads it; no extra inference call.
    """

    score: int  # 0–100
    reasons: list[str] = field(default_factory=list)  # 2–4 bullets (US-JB-05)
    breakdown_md: str = ""
    factors: list[Factor] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class ScoreError(Exception):
    """Typed failure. The message carries the verbatim underlying error —
    never swallowed, never half-succeeded (vision non-negotiable)."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage}] {message}")
