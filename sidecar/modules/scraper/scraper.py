"""scan(): the Scraper's 1 bounded operation, 0 LLM.

Sources are fetched concurrently by a bounded pool, then merged in source order
so the chain stays deterministic: canonicalize, guard broken rows, filters,
optional cap, trust annotation, canonical-URL dedup (FR-SYS-01, first wins). A
failing source lands in its own `errors[]`; a config failure raises
`ScraperError`."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import adapters
from .canonical import canonicalize_url
from .config import PortalsConfig, load_portals
from .filters import (
    passes_company,
    passes_content,
    passes_location,
    passes_salary,
    passes_title,
    passes_visa,
)
from .http import Fetcher
from .quality import assess, is_structurally_broken
from .types import NormalizedJob, ScanPrefs, ScanResult, ScraperError, SourceReport

# Must agree with scorer.deterministic.MIN_JD_CHARS — the scorer floors any
# job under this many description chars at a "missing-data" 0, so a row this
# thin can never be scored; redefined here (not imported) so scraper and
# scorer stay independent modules (the pre-architecture module-silo design).
MIN_JD_CHARS = 200


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
    """1 source's fetch result, carried to the merge. `fetched` is raw output;
    failures land in `report.errors`; `adapter` rides along for enrichment."""

    key: str
    report: SourceReport
    fetched: list[NormalizedJob] = field(default_factory=list)
    adapter: object | None = None


def _fetch_source(
    entry: object, prefs: ScanPrefs, fetcher_factory: Callable[..., Fetcher]
) -> _FetchOutcome:
    """Resolve + fetch ONE source. Pure I/O, no shared state — safe to run in a
    worker thread. Never raises: every source-level failure — typed or not —
    lands in the report (F-H6: one bad feed must not abort the scan)."""
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
    except Exception as e:  # noqa: BLE001 — one bad feed must never abort the scan
        # An adapter bug or a feed shape nobody anticipated (F-H6). Typed
        # failures stay verbatim above; anything else is contained here with
        # its type named, so the per-source diagnostics show the real bug
        # instead of the whole scan (and every scheduled rescan) dying on it.
        report.errors.append(f"unexpected {type(e).__name__}: {e}")
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


# Per-source ceilings on JD detail fetches per scan, 1 HTTP call per JD-less row.
# Search sources already bound their rows at query time (about 120), so all are
# enriched; enumerate sources can page thousands, so they stay capped per source.
SEARCH_ENRICH_CAP: int | None = None  # unbounded — the search itself bounds volume
SCAN_ENRICH_CAP = 100  # per-source ceiling for enumerate-shaped (ATS/board) sources


@dataclass
class _EnrichBucket:
    """One source's JD-less kept rows, awaiting `fetch_detail`. `cap` is
    `SEARCH_ENRICH_CAP` or `SCAN_ENRICH_CAP`, picked by the source's shape."""

    adapter: object
    report: SourceReport
    cap: int | None
    jobs: list[NormalizedJob] = field(default_factory=list)


def _enrich_source(
    bucket: _EnrichBucket,
    prefs: ScanPrefs,
    now: datetime,
    fetcher_factory: Callable[..., Fetcher],
) -> None:
    """Fill JDs for one source's rows (worker-thread body). A failed detail
    fetch records the error and leaves the row's description empty; this
    function itself never drops a row — scan()'s later drop phase discards
    whatever still lacks a scorable description (item 7)."""
    fetcher = fetcher_factory(timeout_s=prefs.timeout_s, usage=bucket.report.usage)
    jobs = bucket.jobs if bucket.cap is None else bucket.jobs[: bucket.cap]
    for job in jobs:
        try:
            detail = bucket.adapter.fetch_detail(job, fetcher)  # type: ignore[attr-defined]
        except ScraperError as e:
            bucket.report.errors.append(f"enrich {job.canonical_url}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 — same containment as _fetch_source (F-H6)
            bucket.report.errors.append(
                f"enrich {job.canonical_url}: unexpected {type(e).__name__}: {e}"
            )
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
    """Run 1 scan over `portals_config` (a path or parsed config). `prefs=None` uses
    the config's tables; `fetcher_factory` is the test seam. Fetched concurrently,
    merged in source order so dedup doesn't depend on which worker finished first."""
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
    kept_pairs: list[tuple[NormalizedJob, SourceReport]] = []
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
            if not passes_content(job.title, job.description, prefs):
                continue
            if not passes_visa(job.description, prefs):
                continue
            if not passes_salary(job.salary, prefs):
                continue
            if not _fresh_enough(job, prefs, now):
                continue
            kept.append(job)
        if broken:
            report.errors.append(f"dropped {broken} structurally broken row(s) (no title/URL)")

        if prefs.per_source_cap > 0:
            kept = kept[: prefs.per_source_cap]

        can_enrich = outcome.adapter is not None and hasattr(outcome.adapter, "fetch_detail")
        is_search = outcome.adapter is not None and hasattr(outcome.adapter, "search")
        cap = SEARCH_ENRICH_CAP if is_search else SCAN_ENRICH_CAP
        for job in kept:
            if job.canonical_url in seen:
                continue
            seen.add(job.canonical_url)
            assess(job, now=now)
            result.jobs.append(job)
            report.kept += 1
            kept_pairs.append((job, report))
            if can_enrich and not job.description:
                bucket = enrich_buckets.setdefault(
                    outcome.key, _EnrichBucket(outcome.adapter, report, cap)
                )
                bucket.jobs.append(job)

    # -- enrich phase (JD-missing rows only; approved-plan #8) --------------
    # Kept rows with no JD get their description via `fetch_detail`, capped per
    # source. A failed fetch records the error and the drop phase below discards the
    # row rather than keep an unscorable husk.
    if enrich_buckets:
        buckets = list(enrich_buckets.values())
        if prefs.max_workers > 1 and len(buckets) > 1:
            with ThreadPoolExecutor(max_workers=prefs.max_workers) as pool:
                list(pool.map(lambda b: _enrich_source(b, prefs, now, fetcher_factory), buckets))
        else:
            for bucket in buckets:
                _enrich_source(bucket, prefs, now, fetcher_factory)

    # -- drop phase (maintainer directive: a row we could not get a
    # description for is discarded, not kept as a husk — best effort exhausted
    # by the enrich phase above). Recorded per source, never tombstoned: a
    # later scan may find the same URL with a real description.
    kept_jobs: list[NormalizedJob] = []
    for job, report in kept_pairs:
        if len(job.description.strip()) < MIN_JD_CHARS:
            report.dropped_no_description.append(job.canonical_url)
            continue
        kept_jobs.append(job)
    result.jobs = kept_jobs

    # -- cross-posting annotation (content identity; after enrich so late-
    # arriving JDs participate). Within-scan only for now — the 90-day
    # scan-history window needs a fingerprint column (schema change, gated
    # on a maintainer checkpoint).

    return result
