"""scan() — the Scraper's one bounded operation (ROADMAP §4).

Zero LLM calls. Sources are **fetched concurrently** by a bounded thread pool
(they are independent, I/O-bound HTTP — a 300-source scan is sub-minute, not
~8 min sequential), then merged **in source order** so the downstream chain
stays deterministic: canonicalize → guard broken rows → title/location/
freshness filters → optional cap → trust annotation → global canonical-URL
dedup (FR-SYS-01, first occurrence wins). A failing source lands in its own
`errors[]` verbatim and never kills the scan (per-source diagnostics are the
point); a config-level failure raises `ScraperError`.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import adapters
from .canonical import canonicalize_url
from .config import PortalsConfig, load_portals
from .filters import passes_location, passes_title
from .http import Fetcher
from .quality import assess, is_structurally_broken
from .types import NormalizedJob, ScanPrefs, ScanResult, ScraperError, SourceReport


def _fresh_enough(job: NormalizedJob, prefs: ScanPrefs, now: datetime) -> bool:
    if prefs.max_age_days <= 0 or not job.posted_at:
        return True  # window off, or source gave no date (quality flags it)
    try:
        posted = datetime.fromisoformat(job.posted_at)
    except ValueError:
        return True
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return posted >= now - timedelta(days=prefs.max_age_days)


@dataclass
class _FetchOutcome:
    """One source's fetch result, carried from the parallel phase to the merge.

    `key`/`report` mirror the sequential design; `fetched` is the raw adapter
    output (filtering/dedup happen later, in source order). A resolution or
    fetch failure is captured in `report.errors` with `fetched=[]`.
    """

    key: str
    report: SourceReport
    fetched: list[NormalizedJob] = field(default_factory=list)


def _fetch_source(
    entry: object, prefs: ScanPrefs, fetcher_factory: Callable[..., Fetcher]
) -> _FetchOutcome:
    """Resolve + fetch ONE source. Pure I/O, no shared state — safe to run in a
    worker thread. Never raises for a source-level failure (recorded in the
    report); only a genuine bug would propagate."""
    resolved = adapters.resolve(entry)  # type: ignore[arg-type]
    if resolved is None:
        return _FetchOutcome(
            key=f"unresolved:{getattr(entry, 'url', '') or getattr(entry, 'board', '')}",
            report=SourceReport(
                errors=[
                    f"no adapter claims this source (url={getattr(entry, 'url', '')!r}, "
                    f"board={getattr(entry, 'board', '')!r}, type={getattr(entry, 'type', '')!r})"
                ]
            ),
        )
    adapter, key = resolved
    report = SourceReport()
    fetcher = fetcher_factory(timeout_s=prefs.timeout_s, usage=report.usage)
    try:
        # Search adapters (LinkedIn/Indeed/Naukri) build queries from prefs;
        # enumerate adapters list a whole feed. Same downstream chain either
        # way (adapters/base.py — the two source shapes).
        if hasattr(adapter, "search"):
            fetched = adapter.search(entry, prefs, fetcher)
        else:
            fetched = adapter.fetch(entry, fetcher)
    except ScraperError as e:
        report.errors.append(str(e))
        return _FetchOutcome(key=key, report=report)
    return _FetchOutcome(key=key, report=report, fetched=fetched)


def _merge_report(result: ScanResult, outcome: _FetchOutcome) -> SourceReport:
    """Attach (or accumulate, if two entries share a key) a source's report."""
    existing = result.per_source.get(outcome.key)
    if existing is None:
        result.per_source[outcome.key] = outcome.report
        return outcome.report
    existing.usage.internal_calls += outcome.report.usage.internal_calls
    existing.usage.latency_ms = (existing.usage.latency_ms or 0) + (
        outcome.report.usage.latency_ms or 0
    )
    existing.errors.extend(outcome.report.errors)
    return existing


def scan(
    portals_config: str | Path | PortalsConfig,
    prefs: ScanPrefs | None = None,
    fetcher_factory: Callable[..., Fetcher] = Fetcher,
) -> ScanResult:
    """Run one scan. `portals_config` is a path (.toml/.json) or a parsed config.

    `prefs=None` uses the config's own filter/scan tables; passing prefs
    overrides them wholesale (the CLI merges flags into config prefs itself).
    `fetcher_factory` is the test seam — same surface as `http.Fetcher`.

    Sources are fetched concurrently (`prefs.max_workers`), then merged in
    source order so dedup (first-occurrence-wins) is deterministic and
    independent of which worker finished first.
    """
    config = (
        portals_config
        if isinstance(portals_config, PortalsConfig)
        else load_portals(portals_config)
    )
    prefs = prefs if prefs is not None else config.prefs
    now = datetime.now(UTC)

    # -- fetch phase (parallel; order-preserving) --------------------------
    sources = config.sources
    if prefs.max_workers > 1 and len(sources) > 1:
        with ThreadPoolExecutor(max_workers=prefs.max_workers) as pool:
            # executor.map preserves input order → deterministic merge below.
            outcomes = list(
                pool.map(lambda e: _fetch_source(e, prefs, fetcher_factory), sources)
            )
    else:
        outcomes = [_fetch_source(e, prefs, fetcher_factory) for e in sources]

    # -- merge phase (sequential, in source order → deterministic dedup) ---
    result = ScanResult()
    seen: set[str] = set()
    for outcome in outcomes:
        report = _merge_report(result, outcome)
        if not outcome.fetched:
            continue
        report.fetched += len(outcome.fetched)
        kept: list[NormalizedJob] = []
        broken = 0
        for job in outcome.fetched:
            job.canonical_url = canonicalize_url(job.canonical_url)
            if is_structurally_broken(job):
                broken += 1
                continue
            if not passes_title(job.title, prefs):
                continue
            if not passes_location(job.location, prefs):
                continue
            if not _fresh_enough(job, prefs, now):
                continue
            kept.append(job)
        if broken:
            report.errors.append(f"dropped {broken} structurally broken row(s) (no title/URL)")

        if prefs.per_source_cap > 0:
            kept = kept[: prefs.per_source_cap]

        for job in kept:
            if job.canonical_url in seen:
                continue
            seen.add(job.canonical_url)
            assess(job, now=now)
            result.jobs.append(job)
            report.kept += 1

    return result
