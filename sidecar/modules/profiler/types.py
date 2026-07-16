"""Profiler module types — plain dataclasses (module convention, ROADMAP §4)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Usage:
    """Aggregate cost record for one bounded operation (ROADMAP §4)."""

    internal_calls: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    usd: float | None = None
    latency_ms: int | None = None
    model: str | None = None


@dataclass
class ProfileResult:
    """Output of one extract_profile() run.

    `profile` is the normalized application-profile record — every key always
    present, empty string / empty list when the resume doesn't state it (the
    grounding rule: never invent). Shape:

    name, first_name, last_name, email, phone, location, country,
    work_authorization, links{label→url}, education[{school, degree,
    discipline, start_year, end_year}]
    """

    profile: dict
    usage: Usage = field(default_factory=Usage)


class ProfileError(Exception):
    """Typed failure — the message carries the verbatim underlying error."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage}] {message}")
