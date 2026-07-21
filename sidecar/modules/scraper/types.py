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
class ContentRule:
    """One `content_filter.by_title_keyword` rule: description allow/block
    lists that apply only when the job title matches `title` (word-boundary,
    same matcher as every other filter)."""

    title: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    block: list[str] = field(default_factory=list)


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
    # Company exclude — a cross-cutting gate like title/location, not a
    # curated-list-removal like career-ops's tracked_companies: a meaningful
    # slice of our adapters are open/uncurated (RemoteOK, TheMuse, Brave,
    # search-shaped adapters), so an unwanted company can surface dynamically
    # the same way an unwanted title or location can (job-finder-preferences
    # design, docs/internal/discovery.md).
    company_block: list[str] = field(default_factory=list)
    # Content filter — description keywords, career-ops's `content_filter`.
    # Empty description always passes (no signal to filter on, same stance as
    # unknown location); block wins over allow.
    content_allow: list[str] = field(default_factory=list)
    content_block: list[str] = field(default_factory=list)
    # Scoped content rules — career-ops's `content_filter.by_title_keyword`:
    # each rule's allow/block applies only to jobs whose title matches the
    # rule's `title` keywords ("for 'manager' roles, block 'on-site'").
    content_by_title: list[ContentRule] = field(default_factory=list)
    # Visa filter — career-ops's `visa_filter`, off by default. When on,
    # drops postings whose description states sponsorship is unavailable
    # (filters.DEFAULT_VISA_PHRASES unless the user supplies their own).
    # For seekers who need sponsorship; empty description passes.
    visa_filter: bool = False
    visa_phrases: list[str] = field(default_factory=list)
    max_age_days: int = 0
    per_source_cap: int = 0
    timeout_s: int = 20
    # Source opt-outs (Settings → Discovery sources). Entries are adapter ids
    # ("greenhouse" disables the family) or full source keys
    # ("apify:memo23/naukri-scraper" disables one entry). Empty = everything on
    # (the default; the user opts out, never in).
    disabled_sources: list[str] = field(default_factory=list)
    # BYO-key credentials for keyed sources ({"apify": token, "brave": key}).
    # Injected IN MEMORY by the scan entrypoint from the sealed store — never
    # part of a durable operation snapshot, never serialized into results.
    credentials: dict[str, str] = field(default_factory=dict)
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
