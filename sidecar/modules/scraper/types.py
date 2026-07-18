"""Scraper module types — plain dataclasses, pre-architecture (ROADMAP §4).

No pydantic yet: the module is a silo; the G4 architecture pass decides the
final type system and these graduate into it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Usage:
    """Aggregate cost record for one bounded operation (ROADMAP §4).

    The Scraper is zero-LLM by contract: `internal_calls` counts HTTP
    requests; `tokens_*`/`usd`/`model` stay None — the cost dashboard reads
    that as "this operation is free beyond bandwidth".
    """

    internal_calls: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    usd: float | None = None
    latency_ms: int | None = None
    model: str | None = None


@dataclass
class NormalizedJob:
    """One normalized posting — the §4 contract row (as built).

    Required: `title`, `canonical_url` (the dedup key, FR-SYS-01). Everything
    else is explicit-empty-allowed (no `?` glyphs — module convention).
    `trust_score`/`trust_flags` carry the ingest quality checks (Track M3
    spec); rank-don't-gate: low trust annotates, it never drops a valid row.
    """

    title: str
    canonical_url: str
    company: str = ""
    location: str = ""
    description: str = ""
    posted_at: str = ""  # ISO 8601 (date or datetime) or ""
    salary: str = ""
    source_adapter: str = ""
    trust_score: int = 100  # 0–100, annotated by quality.assess()
    trust_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanPrefs:
    """User preferences applied by the shared pipeline (not by adapters).

    Filter semantics live in `filters.py` (word-boundary matching — the
    career-ops #1101/#1169 substring lessons). `0` means "off" for the
    numeric knobs: no freshness window, no per-source cap (never self-throttle
    by default — vision ethos; the cap is an explicit user opt-in).
    """

    title_allow: list[str] = field(default_factory=list)
    title_block: list[str] = field(default_factory=list)
    location_allow: list[str] = field(default_factory=list)
    location_block: list[str] = field(default_factory=list)
    location_always_allow: list[str] = field(default_factory=list)
    max_age_days: int = 0
    per_source_cap: int = 0
    timeout_s: int = 20
    # Sources are independent, I/O-bound HTTP — fetched concurrently by a bounded
    # pool so a 300-source scan is sub-minute, not ~8 min sequential. The cap
    # keeps socket/latency pressure sane; `<= 1` forces the deterministic
    # sequential path (debugging). Dedup order stays deterministic regardless
    # (results merge in source order — see scraper.scan).
    max_workers: int = 8


@dataclass
class SourceReport:
    """Per-source diagnostics for one scan (ROADMAP §4: `{usage, errors[]}`).

    `fetched`/`kept` make filter/dedup attrition visible per source;
    errors carry verbatim messages — never swallowed (vision non-negotiable).
    """

    usage: Usage = field(default_factory=Usage)
    errors: list[str] = field(default_factory=list)
    fetched: int = 0
    kept: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanResult:
    """Output of one scan() operation: deduped jobs + per-source diagnostics,

    keyed by source key (`<adapter>:<tenant-or-host>`).
    """

    jobs: list[NormalizedJob] = field(default_factory=list)
    per_source: dict[str, SourceReport] = field(default_factory=dict)


class ScraperError(Exception):
    """Typed failure. The message carries the verbatim underlying error —
    never swallowed, never half-succeeded (vision non-negotiable)."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage}] {message}")
