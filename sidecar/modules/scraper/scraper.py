"""scan() — the Scraper's one bounded operation (ROADMAP §4).

Zero LLM calls. Per source: resolve adapter → fetch → guard broken rows →
canonicalize → title/location/freshness filters → optional cap → trust
annotation → global canonical-URL dedup (FR-SYS-01, first occurrence wins).
A failing source lands in its own `errors[]` verbatim and never kills the
scan (per-source diagnostics are the point); a config-level failure raises
`ScraperError`.
"""

from __future__ import annotations

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


def scan(
    portals_config: str | Path | PortalsConfig,
    prefs: ScanPrefs | None = None,
    fetcher_factory: type[Fetcher] = Fetcher,
) -> ScanResult:
    """Run one scan. `portals_config` is a path (.toml/.json) or a parsed config.

    `prefs=None` uses the config's own filter/scan tables; passing prefs
    overrides them wholesale (the CLI merges flags into config prefs itself).
    `fetcher_factory` is the test seam — same surface as `http.Fetcher`.
    """
    config = (
        portals_config
        if isinstance(portals_config, PortalsConfig)
        else load_portals(portals_config)
    )
    prefs = prefs if prefs is not None else config.prefs
    now = datetime.now(UTC)

    result = ScanResult()
    seen: set[str] = set()

    for entry in config.sources:
        resolved = adapters.resolve(entry)
        if resolved is None:
            key = f"unresolved:{entry.url or entry.board}"
            result.per_source[key] = SourceReport(
                errors=[
                    f"no adapter claims this source (url={entry.url!r}, "
                    f"board={entry.board!r}, type={entry.type!r})"
                ]
            )
            continue
        adapter, key = resolved
        report = result.per_source.setdefault(key, SourceReport())
        fetcher = fetcher_factory(timeout_s=prefs.timeout_s, usage=report.usage)

        try:
            fetched = adapter.fetch(entry, fetcher)
        except ScraperError as e:
            report.errors.append(str(e))
            continue

        report.fetched += len(fetched)
        kept: list[NormalizedJob] = []
        broken = 0
        for job in fetched:
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
