"""HTTP fetcher — stdlib-only, mirroring `_shared/job_input.py`'s precedent.

One `Fetcher` instance per source: it counts requests into the source's
`Usage` (§4 usage record — the Scraper's "cost" is HTTP calls, not tokens).
Tests inject a `FakeFetcher` with the same surface; adapters never touch
urllib directly.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .types import ScraperError, Usage

USER_AGENT = "findsyoujobs/0.0 (+https://github.com/findsyoujobs)"
MAX_BYTES = 20 * 1024 * 1024  # refuse absurd payloads before json.loads


class Fetcher:
    """GET text/JSON over http(s), recording call count + latency into `usage`."""

    def __init__(self, timeout_s: int = 20, usage: Usage | None = None) -> None:
        self.timeout_s = timeout_s
        self.usage = usage if usage is not None else Usage()

    def _read(self, url: str, data: bytes | None = None, content_type: str = "") -> str:
        if not url.startswith(("http://", "https://")):
            raise ScraperError("fetch", f"refusing non-http(s) URL: {url}")
        headers = {"User-Agent": USER_AGENT}
        if content_type:
            headers["Content-Type"] = content_type
        # Scheme constrained to http(s) above (same rationale as job_input.py).
        req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                body = resp.read(MAX_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ScraperError("fetch", f"could not fetch {url}: {e}") from e
        finally:
            self.usage.internal_calls += 1
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.usage.latency_ms = (self.usage.latency_ms or 0) + elapsed_ms
        if len(body) > MAX_BYTES:
            raise ScraperError("fetch", f"{url} returned more than {MAX_BYTES} bytes; refusing")
        return body.decode("utf-8", errors="replace")

    def get_text(self, url: str) -> str:
        return self._read(url)

    def get_json(self, url: str) -> object:
        return self._parse_json(url, self.get_text(url))

    def post_json(self, url: str, payload: object) -> object:
        """POST a JSON body, return parsed JSON. Exists for Workday-style CxS
        endpoints whose public job lists answer only to POST — same guards,
        same usage accounting as GET (one internal_call per request)."""
        body = self._read(
            url, data=json.dumps(payload).encode(), content_type="application/json"
        )
        return self._parse_json(url, body)

    def _parse_json(self, url: str, text: str) -> object:
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ScraperError("fetch", f"{url} did not return valid JSON: {e}") from e
