"""Covers: the parallel fetch phase of scan() (discovery-expansion 2026-07-18,
user directive — parallelism is the foundation the rest of discovery builds on).

The guarantees under test:
- concurrency is real — a slow source does not serialize the others (total
  wall-clock ≈ the slowest source, not the sum);
- results are deterministic and identical to the sequential path, regardless
  of which worker finishes first (dedup is first-occurrence-wins in SOURCE
  order, not completion order);
- `max_workers <= 1` forces the sequential path.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from sidecar.modules.scraper.config import PortalsConfig, SourceEntry
from sidecar.modules.scraper.http import Fetcher
from sidecar.modules.scraper.scraper import scan
from sidecar.modules.scraper.types import NormalizedJob, ScanPrefs, Usage


def _fresh_fetcher(timeout_s: int = 20, usage: object = None) -> Fetcher:
    """A per-source Fetcher with its own Usage (the scan()-side factory seam)."""
    return Fetcher(usage=Usage())


def _job(url: str, title: str = "Backend Engineer") -> NormalizedJob:
    return NormalizedJob(title=title, canonical_url=url, location="Remote")


class _SleepAdapter:
    """A fake adapter module: sleeps `delay`, then returns its canned rows.
    Registered via monkeypatching adapters.resolve so we control timing."""

    def __init__(self, adapter_id: str, delay: float, jobs: list[NormalizedJob]) -> None:
        self.ID = adapter_id
        self._delay = delay
        self._jobs = jobs

    def detect(self, entry: SourceEntry) -> str:  # pragma: no cover - unused
        return self.ID

    def fetch(self, entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
        time.sleep(self._delay)
        fetcher.usage.internal_calls += 1
        return list(self._jobs)


def _resolver(mapping: Mapping[str, tuple[Any, str]]):
    """Return a fake adapters.resolve keyed by the entry's board value."""

    def resolve(entry: SourceEntry):
        return mapping.get(entry.board)

    return resolve


def test_parallel_is_faster_than_sequential_and_deterministic(monkeypatch) -> None:
    # Three sources, each sleeping 0.3s. Sequential ≈0.9s; parallel ≈0.3s.
    a = _SleepAdapter("a", 0.3, [_job("https://x/a1"), _job("https://x/shared")])
    b = _SleepAdapter("b", 0.3, [_job("https://x/b1"), _job("https://x/shared")])
    c = _SleepAdapter("c", 0.3, [_job("https://x/c1")])
    mapping = {"a": (a, "a:a"), "b": (b, "b:b"), "c": (c, "c:c")}
    monkeypatch.setattr("sidecar.modules.scraper.scraper.adapters.resolve", _resolver(mapping))

    config = PortalsConfig(
        sources=[SourceEntry(board="a"), SourceEntry(board="b"), SourceEntry(board="c")],
        prefs=ScanPrefs(max_workers=4),
    )

    started = time.monotonic()
    result = scan(config, fetcher_factory=_fresh_fetcher)
    elapsed = time.monotonic() - started

    # Real concurrency: well under the 0.9s sequential sum.
    assert elapsed < 0.7, f"parallel scan took {elapsed:.2f}s — not concurrent"

    # Deterministic dedup: 'shared' is fetched by both a and b, but a comes
    # first in source order so a's copy wins regardless of finish order.
    urls = [j.canonical_url for j in result.jobs]
    assert urls == ["https://x/a1", "https://x/shared", "https://x/b1", "https://x/c1"]
    assert result.per_source["a:a"].kept == 2
    assert result.per_source["b:b"].kept == 1  # 'shared' deduped against a's


def test_sequential_path_matches_parallel(monkeypatch) -> None:
    a = _SleepAdapter("a", 0.0, [_job("https://x/a1"), _job("https://x/shared")])
    b = _SleepAdapter("b", 0.0, [_job("https://x/shared"), _job("https://x/b1")])
    mapping = {"a": (a, "a:a"), "b": (b, "b:b")}
    monkeypatch.setattr("sidecar.modules.scraper.scraper.adapters.resolve", _resolver(mapping))
    sources = [SourceEntry(board="a"), SourceEntry(board="b")]

    def run(workers: int):
        cfg = PortalsConfig(sources=list(sources), prefs=ScanPrefs(max_workers=workers))
        return [
            j.canonical_url
            for j in scan(cfg, fetcher_factory=_fresh_fetcher).jobs
        ]

    assert run(1) == run(8) == ["https://x/a1", "https://x/shared", "https://x/b1"]


def test_one_failing_source_does_not_sink_the_scan(monkeypatch) -> None:
    from sidecar.modules.scraper.types import ScraperError

    class _BoomAdapter(_SleepAdapter):
        def fetch(self, entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
            raise ScraperError("boom", "429 rate limited")

    ok = _SleepAdapter("ok", 0.0, [_job("https://x/ok1")])
    boom = _BoomAdapter("boom", 0.0, [])
    mapping = {"ok": (ok, "ok:ok"), "boom": (boom, "boom:boom")}
    monkeypatch.setattr("sidecar.modules.scraper.scraper.adapters.resolve", _resolver(mapping))

    config = PortalsConfig(
        sources=[SourceEntry(board="boom"), SourceEntry(board="ok")],
        prefs=ScanPrefs(max_workers=4),
    )
    result = scan(config, fetcher_factory=_fresh_fetcher)
    # The good source still lands; the bad one carries its verbatim error.
    assert [j.canonical_url for j in result.jobs] == ["https://x/ok1"]
    assert "429 rate limited" in result.per_source["boom:boom"].errors[0]
