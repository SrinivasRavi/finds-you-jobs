"""Scraper module types — plain dataclasses, pre-architecture (ROADMAP section 4).

No pydantic yet: the module is a silo; the G4 architecture pass decides the
final type system and these graduate into it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field


@dataclass
class Usage:
    """Cost record for 1 scan: 0 LLM by contract, so `internal_calls` counts HTTP
    requests and tokens, usd and model stay None."""

    internal_calls: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    usd: float | None = None
    latency_ms: int | None = None
    model: str | None = None


@dataclass
class NormalizedJob:
    """1 normalized posting, the section 4 contract row. `title` is required; a
    scanned row also carries `canonical_url` (FR-SYS-01). Everything else may be
    empty. `trust_score`/`trust_flags` annotate; low trust never drops a row."""

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
    """User preferences applied by the shared pipeline, not by adapters. Matching
    lives in `filters.py`. `0` turns a numeric knob off: no freshness window, no
    per-source cap (never self-throttle by default)."""

    title_allow: list[str] = field(default_factory=list)
    title_block: list[str] = field(default_factory=list)
    location_allow: list[str] = field(default_factory=list)
    location_block: list[str] = field(default_factory=list)
    location_always_allow: list[str] = field(default_factory=list)
    # True when a posting's place lies inside a wanted one (a city in a wanted state,
    # metro or country); set from the user's picked places, never from a snapshot.
    location_match: Callable[[str], bool] | None = None
    # Company exclude: a cross-cutting gate like title and location, since open
    # adapters (RemoteOK, TheMuse, Brave, search) surface companies dynamically
    # (docs/internal/discovery.md).
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
    # Salary filter (career-ops's `salary_filter`, via `salary.py`). 0 turns a bound
    # off; amounts are annual in `salary_currency`. A posting in another currency, or
    # with no parsable salary, always passes (rank, don't gate).
    salary_min: int = 0
    salary_max: int = 0
    salary_currency: str = ""
    # User-authored search terms (constrained free-form `search_queries` —
    # design locked 2026-07-21): each term rides the search adapters' existing
    # query templates as an extra location-less keyword (Brave keeps its
    # ATS_SITES allowlist; parsers keep their graceful fallbacks). Never raw
    # query syntax with its own `site:` targets.
    search_terms: list[str] = field(default_factory=list)
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
    """Per-source diagnostics for 1 scan: `fetched` and `kept` show attrition, and
    errors are verbatim. `dropped_no_description` lists URLs discarded after enrich
    for lacking a scorable JD, never tombstoned."""

    usage: Usage = field(default_factory=Usage)
    errors: list[str] = field(default_factory=list)
    fetched: int = 0
    kept: int = 0
    dropped_no_description: list[str] = field(default_factory=list)

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


class RateLimitError(ScraperError):
    """HTTP 429, or its in-memory cool-down (F-M9). Typed so callers can tell "back
    off" from a broken feed, yet still a ScraperError. `retry_after_s` is the
    server's hint or the default."""

    def __init__(self, url: str, retry_after_s: float, *, cooling_down: bool = False) -> None:
        self.retry_after_s = retry_after_s
        detail = (
            f"cooling down after an earlier HTTP 429 (retry in ~{retry_after_s:.0f}s)"
            if cooling_down
            else f"HTTP 429 rate-limited (retry after ~{retry_after_s:.0f}s)"
        )
        super().__init__("rate-limit", f"{url}: {detail}")
