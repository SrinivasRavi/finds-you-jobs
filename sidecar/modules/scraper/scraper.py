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
from .filters import passes_company, passes_content, passes_location, passes_title
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
    fetch failure is captured in `report.errors` with `fetched=[]`. `adapter`
    rides along for the enrichment phase (`fetch_detail`)."""

    key: str
    report: SourceReport
    fetched: list[NormalizedJob] = field(default_factory=list)
    adapter: object | None = None


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
        return _FetchOutcome(key=key, report=report, adapter=adapter)
    return _FetchOutcome(key=key, report=report, fetched=fetched, adapter=adapter)


def _is_disabled(entry: object, disabled: set[str]) -> bool:
    """True when the entry's adapter family (`greenhouse`) or its full source
    key (`greenhouse:gleanwork`) is in the user's opt-out list."""
    resolved = adapters.resolve(entry)  # type: ignore[arg-type]
    if resolved is None:
        return False
    adapter, key = resolved
    return adapter.ID in disabled or key in disabled


# Per-source ceiling on JD detail fetches per scan (enrichment is one HTTP
# call per JD-less row — the cap keeps a 200-row Workday tenant from turning
# one scan into 200 detail requests). Rows past the cap keep their missing-JD
# flag and score via the lenient path.
ENRICH_CAP = 20


@dataclass
class _EnrichBucket:
    """One source's JD-less kept rows, awaiting `fetch_detail`."""

    adapter: object
    report: SourceReport
    jobs: list[NormalizedJob] = field(default_factory=list)


def _enrich_source(
    bucket: _EnrichBucket,
    prefs: ScanPrefs,
    now: datetime,
    fetcher_factory: Callable[..., Fetcher],
) -> None:
    """Fill JDs for one source's rows (worker-thread body). A failed detail
    fetch records the error and keeps the row — enrichment never drops."""
    fetcher = fetcher_factory(timeout_s=prefs.timeout_s, usage=bucket.report.usage)
    for job in bucket.jobs[:ENRICH_CAP]:
        try:
            detail = bucket.adapter.fetch_detail(job, fetcher)  # type: ignore[attr-defined]
        except ScraperError as e:
            bucket.report.errors.append(f"enrich {job.canonical_url}: {e}")
            continue
        if detail:
            job.description = detail
            assess(job, now=now)  # re-annotate: JD-dependent flags now real


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
    # Source opt-outs (Settings → Discovery sources): a disabled family/entry
    # is skipped before any fetch — zero requests, no per-source row. An entry
    # no adapter claims is kept so its "unresolved" diagnostic stays visible.
    sources = config.sources
    if prefs.disabled_sources:
        disabled = set(prefs.disabled_sources)
        sources = [e for e in sources if not _is_disabled(e, disabled)]
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
    enrich_buckets: dict[str, _EnrichBucket] = {}
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
            if not passes_company(job.company, prefs):
                continue
            # Description may not be populated yet for sources that need the
            # in-scan enrich phase (guest HTML) — empty always passes, so this
            # filter only has effect where the description already arrived
            # with the initial fetch (most ATS adapters). Same partial-coverage
            # stance career-ops's own content_filter documents.
            if not passes_content(job.description, prefs):
                continue
            if not _fresh_enough(job, prefs, now):
                continue
            kept.append(job)
        if broken:
            report.errors.append(f"dropped {broken} structurally broken row(s) (no title/URL)")

        if prefs.per_source_cap > 0:
            kept = kept[: prefs.per_source_cap]

        can_enrich = outcome.adapter is not None and hasattr(outcome.adapter, "fetch_detail")
        for job in kept:
            if job.canonical_url in seen:
                continue
            seen.add(job.canonical_url)
            assess(job, now=now)
            result.jobs.append(job)
            report.kept += 1
            if can_enrich and not job.description:
                bucket = enrich_buckets.setdefault(
                    outcome.key, _EnrichBucket(outcome.adapter, report)
                )
                bucket.jobs.append(job)

    # -- enrich phase (JD-missing rows only; approved-plan #8) --------------
    # Kept rows with no JD get their real description fetched per adapter
    # (`fetch_detail`) so scoring runs normally — the optimal path. Bounded at
    # ENRICH_CAP per source; a failed detail fetch keeps the row (the lenient
    # alias+location match already admitted it) and records the error.
    if enrich_buckets:
        buckets = list(enrich_buckets.values())
        if prefs.max_workers > 1 and len(buckets) > 1:
            with ThreadPoolExecutor(max_workers=prefs.max_workers) as pool:
                list(pool.map(lambda b: _enrich_source(b, prefs, now, fetcher_factory), buckets))
        else:
            for bucket in buckets:
                _enrich_source(bucket, prefs, now, fetcher_factory)

    return result
