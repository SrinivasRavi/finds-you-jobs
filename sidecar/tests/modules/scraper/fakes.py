"""Test fakes shared by the scraper test files — no live network anywhere.

Covers:
  US-JB-01 — scored daily feed (the scan feeds it)
  US-SYS-01 / FR-SYS-01 — canonical-URL dedup
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

from sidecar.modules.scraper.http import Fetcher
from sidecar.modules.scraper.types import ScraperError, Usage

PAYLOADS = Path(__file__).resolve().parent / "payloads"


class FakeFetcher(Fetcher):
    """Same surface as http.Fetcher; serves canned payloads keyed by URL substring.

    `routes` maps a URL substring → payload file name (under payloads/), a
    dict/list (returned as-is), or an Exception instance (raised).
    """

    routes: dict[str, object] = {}

    def __init__(self, timeout_s: int = 20, usage: Usage | None = None) -> None:
        self.timeout_s = timeout_s
        self.usage = usage if usage is not None else Usage()

    def _lookup(self, url: str) -> object:
        self.usage.internal_calls += 1
        self.usage.latency_ms = (self.usage.latency_ms or 0) + 1
        _ = time.monotonic()  # parity with the real fetcher's timing path
        for fragment, payload in self.routes.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                if isinstance(payload, str):
                    return (PAYLOADS / payload).read_text()
                return payload
        raise ScraperError("fetch", f"FakeFetcher has no route for {url}")

    def get_text(self, url: str) -> str:
        payload = self._lookup(url)
        if isinstance(payload, str):
            return payload
        return json.dumps(payload)

    def get_json(self, url: str) -> object:
        payload = self._lookup(url)
        if isinstance(payload, str):
            return json.loads(payload)
        return payload


def routed(routes: Mapping[str, object]) -> type[FakeFetcher]:
    """Build a FakeFetcher subclass bound to `routes` (scan takes a factory)."""
    return type("RoutedFakeFetcher", (FakeFetcher,), {"routes": routes})
