"""HTTP fetcher — stdlib-only, mirroring `_shared/job_input.py`'s precedent.

One `Fetcher` instance per source: it counts requests into the source's
`Usage` (section 4 usage record — the Scraper's "cost" is HTTP calls, not tokens).
Tests inject a `FakeFetcher` with the same surface; adapters never touch
urllib directly.
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .._shared.url_guard import GuardedRedirects as _GuardedRedirects
from .._shared.url_guard import url_refusal
from .types import RateLimitError, ScraperError, Usage

USER_AGENT = "findsyoujobs/0.0 (+https://github.com/findsyoujobs)"
MAX_BYTES = 20 * 1024 * 1024  # refuse absurd payloads before json.loads

# Header policy: keyless ATS and board adapters send the honest UA above. The
# big search boards refuse it, so search adapters send `BROWSER_HEADERS`.
# Browser-standard headers at 1 user's desktop volume is defensible; proxy
# rotation, CAPTCHA solving and TLS forgery are the arms race we stay out of.
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# SSRF guard (F-M1): the refusal logic and guarded redirects live in
# `_shared/url_guard.py`; refusals are wrapped in ScraperError below.
# ---------------------------------------------------------------------------

_opener = urllib.request.build_opener(_GuardedRedirects)


# ---------------------------------------------------------------------------
# Rate limits (F-M9): a 429 raises RateLimitError and opens a per-host cool-down
# so the next scan skips the host. Process-local: a restart forgiving it is
# proportionate for a single-user desktop app.
# ---------------------------------------------------------------------------

_COOLDOWN_DEFAULT_S = 60.0
_COOLDOWN_MAX_S = 15 * 60.0  # a hostile Retry-After can't brick a source for days
_cooldown_lock = threading.Lock()
_cooldowns: dict[str, float] = {}  # host → time.monotonic() deadline

# Jittered pause between a paginating adapter's page requests (F-M9) — keeps a
# MAX_PAGES loop from reading as burst traffic to one host. Adapters run on
# scan()'s worker threads (never the event loop), so time.sleep is safe here.
PAGE_PAUSE_RANGE_S: tuple[float, float] = (0.3, 0.9)


def page_pause() -> None:
    """Sleep a small jittered interval between page fetches (worker threads)."""
    time.sleep(random.uniform(*PAGE_PAUSE_RANGE_S))  # noqa: S311 — jitter, not crypto


def paced_pages(pages: Iterable[int], *, already_requested: bool = False) -> Iterator[int]:
    """Yield each page of a bounded paginating loop, pausing before every request
    but the first (D-M9). `already_requested=True` when this host was already hit
    this run (the LinkedIn guest adapter's later queries)."""
    for i, page in enumerate(pages):
        if i or already_requested:
            page_pause()
        yield page


def _retry_after_s(value: str | None) -> float:
    """Seconds from a Retry-After header (delta or HTTP-date), clamped to
    [0, _COOLDOWN_MAX_S]; the default cool-down when absent/unparseable."""
    if not value:
        return _COOLDOWN_DEFAULT_S
    value = value.strip()
    if value.isdigit():
        return min(float(value), _COOLDOWN_MAX_S)
    try:
        dt = parsedate_to_datetime(value)
    except ValueError:
        return _COOLDOWN_DEFAULT_S
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = (dt - datetime.now(UTC)).total_seconds()
    if delta <= 0:
        return _COOLDOWN_DEFAULT_S
    return min(delta, _COOLDOWN_MAX_S)


def _cooldown_remaining(host: str) -> float:
    with _cooldown_lock:
        return _cooldowns.get(host, 0.0) - time.monotonic()


def _start_cooldown(host: str, seconds: float) -> None:
    with _cooldown_lock:
        _cooldowns[host] = max(_cooldowns.get(host, 0.0), time.monotonic() + seconds)


class Fetcher:
    """GET text/JSON over http(s), recording call count + latency into `usage`."""

    def __init__(self, timeout_s: int = 20, usage: Usage | None = None) -> None:
        self.timeout_s = timeout_s
        self.usage = usage if usage is not None else Usage()

    def _read(
        self,
        url: str,
        data: bytes | None = None,
        content_type: str = "",
        headers: dict[str, str] | None = None,
        timeout_s: int | None = None,
    ) -> str:
        if not url.startswith(("http://", "https://")):
            raise ScraperError("fetch", f"refusing non-http(s) URL: {url}")
        refusal = url_refusal(url)  # SSRF guard (F-M1) — resolved-IP check
        if refusal:
            raise ScraperError("fetch", f"refusing to fetch {url}: {refusal}")
        host = urlsplit(url).hostname or ""
        remaining = _cooldown_remaining(host)  # F-M9 — no request during cool-down
        if remaining > 0:
            raise RateLimitError(url, remaining, cooling_down=True)
        # `headers` (e.g. BROWSER_HEADERS for search adapters) replaces the
        # default honest UA; callers own the policy choice (see module notes).
        merged = dict(headers) if headers else {"User-Agent": USER_AGENT}
        if content_type:
            merged["Content-Type"] = content_type
        # Scheme constrained to http(s) above (same rationale as job_input.py).
        req = urllib.request.Request(url, data=data, headers=merged)  # noqa: S310
        started = time.monotonic()
        try:
            with _opener.open(  # noqa: S310 — scheme + resolved-IP checked above
                req, timeout=timeout_s if timeout_s is not None else self.timeout_s
            ) as resp:
                body = resp.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            if e.code == 429:  # typed rate-limit + cool-down (F-M9)
                retry_after = _retry_after_s(e.headers.get("Retry-After"))
                _start_cooldown(host, retry_after)
                raise RateLimitError(url, retry_after) from e
            raise ScraperError("fetch", f"could not fetch {url}: {e}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ScraperError("fetch", f"could not fetch {url}: {e}") from e
        finally:
            self.usage.internal_calls += 1
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.usage.latency_ms = (self.usage.latency_ms or 0) + elapsed_ms
        if len(body) > MAX_BYTES:
            raise ScraperError("fetch", f"{url} returned more than {MAX_BYTES} bytes; refusing")
        return body.decode("utf-8", errors="replace")

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        return self._read(url, headers=headers)

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> object:
        return self._parse_json(url, self.get_text(url, headers=headers))

    def post_json(
        self,
        url: str,
        payload: object,
        headers: dict[str, str] | None = None,
        timeout_s: int | None = None,
    ) -> object:
        """POST a JSON body and return parsed JSON, for Workday-style endpoints; same
        guards and accounting as GET. `headers` carries a keyed source's token (never
        the URL); `timeout_s` serves long calls like Apify's run-sync."""
        body = self._read(
            url,
            data=json.dumps(payload).encode(),
            content_type="application/json",
            headers=headers,
            timeout_s=timeout_s,
        )
        return self._parse_json(url, body)

    def _parse_json(self, url: str, text: str) -> object:
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ScraperError("fetch", f"{url} did not return valid JSON: {e}") from e
