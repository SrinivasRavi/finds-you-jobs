"""Fetch-layer guards (technical-audit F-M1 SSRF, F-M9 rate-limit) — zero
network: DNS resolution and the urllib opener are both faked.

Covers:
  F-M1 — private/loopback/link-local/metadata targets refused before any
         request; every redirect hop re-validated; public hosts unaffected
  F-M9 — HTTP 429 → typed RateLimitError with Retry-After honored, plus the
         in-memory per-host cool-down (no re-burst until it expires)
"""

from __future__ import annotations

import email.message
import io
import socket
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.client import HTTPMessage

import pytest

from sidecar.modules._shared import url_guard
from sidecar.modules.scraper import http
from sidecar.modules.scraper.http import MAX_BYTES, Fetcher
from sidecar.modules.scraper.types import RateLimitError, ScraperError

# ---------------------------------------------------------------------------
# Fakes — resolution + opener seams
# ---------------------------------------------------------------------------


def _resolving_to(mapping: dict[str, str]):
    """A getaddrinfo fake: hostname → one address (IP literals echo back)."""

    def fake_getaddrinfo(host: str, port: object, *args: object, **kw: object) -> list:
        addr = mapping.get(host, host)  # unknown hosts treated as literals
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))]

    return fake_getaddrinfo


class _FakeResponse:
    def __init__(self, body: bytes = b"ok") -> None:
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeOpener:
    """Stands in for http._opener; counts calls, returns/raises on demand."""

    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None):
        self.calls = 0
        self._response = response if response is not None else _FakeResponse()
        self._exc = exc

    def open(self, req: urllib.request.Request, timeout: object = None) -> _FakeResponse:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._response


def _http_429(url: str, retry_after: str | None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(url, 429, "Too Many Requests", headers, io.BytesIO(b""))


# ---------------------------------------------------------------------------
# F-M1 — SSRF guard on the direct fetch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://127.0.0.1:8000/secrets",  # loopback
        "http://10.0.0.7/admin",  # RFC1918
        "http://192.168.1.1/router",  # RFC1918
        "http://[::1]/",  # IPv6 loopback
        "http://0.0.0.0/",  # unspecified
    ],
)
def test_private_ip_literals_refused_before_any_request(monkeypatch, url) -> None:
    opener = _FakeOpener()
    monkeypatch.setattr(http, "_opener", opener)
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _resolving_to({}))
    with pytest.raises(ScraperError, match="non-public"):
        Fetcher().get_text(url)
    assert opener.calls == 0  # refused before the request, not after


def test_localhost_and_dot_local_refused_without_resolving(monkeypatch) -> None:
    opener = _FakeOpener()
    monkeypatch.setattr(http, "_opener", opener)

    def explode(*a: object, **kw: object) -> list:
        raise AssertionError("must not resolve a known-local name")

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", explode)
    for url in ("http://localhost:1420/", "http://printer.local/"):
        with pytest.raises(ScraperError, match="loopback/.local"):
            Fetcher().get_text(url)
    assert opener.calls == 0


def test_hostname_resolving_to_private_address_refused(monkeypatch) -> None:
    opener = _FakeOpener()
    monkeypatch.setattr(http, "_opener", opener)
    monkeypatch.setattr(
        url_guard.socket, "getaddrinfo", _resolving_to({"internal.corp.example": "10.1.2.3"})
    )
    with pytest.raises(ScraperError, match="non-public"):
        Fetcher().get_text("https://internal.corp.example/jobs")
    assert opener.calls == 0


def test_unresolvable_host_is_typed_error(monkeypatch) -> None:
    def fail(*a: object, **kw: object) -> list:
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(http, "_opener", _FakeOpener())
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", fail)
    with pytest.raises(ScraperError, match="cannot resolve"):
        Fetcher().get_text("https://no-such-host.example/")


def test_public_host_fetches_normally(monkeypatch) -> None:
    opener = _FakeOpener(_FakeResponse(b"<html>jobs</html>"))
    monkeypatch.setattr(http, "_opener", opener)
    monkeypatch.setattr(
        url_guard.socket, "getaddrinfo", _resolving_to({"boards.example.com": "93.184.216.34"})
    )
    fetcher = Fetcher()
    assert fetcher.get_text("https://boards.example.com/acme") == "<html>jobs</html>"
    assert opener.calls == 1
    assert fetcher.usage.internal_calls == 1


# ---------------------------------------------------------------------------
# F-M1 — redirect hops re-validated
# ---------------------------------------------------------------------------


def test_redirect_to_private_address_refused(monkeypatch) -> None:
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _resolving_to({}))
    handler = http._GuardedRedirects()
    req = urllib.request.Request("https://boards.example.com/acme")
    with pytest.raises(urllib.error.URLError, match="refusing redirect"):
        handler.redirect_request(
            req, io.BytesIO(b""), 302, "Found", HTTPMessage(), "http://169.254.169.254/latest/meta-data/"
        )


def test_redirect_public_to_public_allowed(monkeypatch) -> None:
    monkeypatch.setattr(
        url_guard.socket,
        "getaddrinfo",
        _resolving_to({"boards.example.com": "93.184.216.34", "jobs.example.org": "142.250.183.9"}),
    )
    handler = http._GuardedRedirects()
    req = urllib.request.Request("https://boards.example.com/acme")
    new_req = handler.redirect_request(
        req, io.BytesIO(b""), 302, "Found", HTTPMessage(), "https://jobs.example.org/acme/123"
    )
    assert new_req is not None
    assert new_req.full_url == "https://jobs.example.org/acme/123"


# ---------------------------------------------------------------------------
# F-M9 — 429 → typed error, Retry-After, per-host cool-down
# ---------------------------------------------------------------------------


def _public(monkeypatch, host: str = "api.example.com") -> None:
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _resolving_to({host: "142.250.183.5"}))


def test_429_raises_typed_rate_limit_error_with_retry_after(monkeypatch) -> None:
    url = "https://api.example.com/jobs"
    _public(monkeypatch)
    monkeypatch.setattr(http, "_opener", _FakeOpener(exc=_http_429(url, "7")))
    with pytest.raises(RateLimitError) as ei:
        Fetcher().get_text(url)
    assert ei.value.retry_after_s == 7.0
    assert "429" in str(ei.value)
    assert "[rate-limit]" in str(ei.value)


def test_429_opens_cooldown_and_next_fetch_skips_the_host(monkeypatch) -> None:
    url = "https://api.example.com/jobs"
    _public(monkeypatch)
    opener = _FakeOpener(exc=_http_429(url, "300"))
    monkeypatch.setattr(http, "_opener", opener)
    with pytest.raises(RateLimitError):
        Fetcher().get_text(url)
    assert opener.calls == 1
    # Same host, second attempt: no request at all — the cool-down answers.
    with pytest.raises(RateLimitError, match="cooling down"):
        Fetcher().get_text("https://api.example.com/other-path")
    assert opener.calls == 1
    # A different host is unaffected.
    _public(monkeypatch, "other.example.net")
    ok_opener = _FakeOpener(_FakeResponse(b"fine"))
    monkeypatch.setattr(http, "_opener", ok_opener)
    assert Fetcher().get_text("https://other.example.net/jobs") == "fine"


def test_retry_after_parsing_delta_date_and_clamps() -> None:
    assert http._retry_after_s(None) == http._COOLDOWN_DEFAULT_S
    assert http._retry_after_s("120") == 120.0
    assert http._retry_after_s("999999") == http._COOLDOWN_MAX_S  # hostile header clamped
    assert http._retry_after_s("garbage") == http._COOLDOWN_DEFAULT_S
    http_date = format_datetime(datetime.now(UTC) + timedelta(seconds=300))
    assert 250.0 < http._retry_after_s(http_date) <= 300.0
    past = format_datetime(datetime.now(UTC) - timedelta(seconds=300))
    assert http._retry_after_s(past) == http._COOLDOWN_DEFAULT_S


def test_non_429_http_error_stays_generic_scraper_error(monkeypatch) -> None:
    url = "https://api.example.com/jobs"
    _public(monkeypatch)
    headers = email.message.Message()
    boom = urllib.error.HTTPError(url, 503, "Service Unavailable", headers, io.BytesIO(b""))
    monkeypatch.setattr(http, "_opener", _FakeOpener(exc=boom))
    with pytest.raises(ScraperError, match="could not fetch") as ei:
        Fetcher().get_text(url)
    assert not isinstance(ei.value, RateLimitError)


# ---------------------------------------------------------------------------
# Existing contracts still hold through the new opener path
# ---------------------------------------------------------------------------


def test_body_over_cap_refused(monkeypatch) -> None:
    _public(monkeypatch, "big.example.com")
    monkeypatch.setattr(http, "_opener", _FakeOpener(_FakeResponse(b"x" * (MAX_BYTES + 1))))
    with pytest.raises(ScraperError, match="more than"):
        Fetcher().get_text("https://big.example.com/huge")


def test_non_http_scheme_still_refused() -> None:
    with pytest.raises(ScraperError, match="non-http"):
        Fetcher().get_text("ftp://example.com/x")


# ---------------------------------------------------------------------------
# 2026-07-25 dedup — both fetch layers run the ONE guard in _shared/url_guard
# ---------------------------------------------------------------------------


def test_guard_is_the_single_shared_copy() -> None:
    from sidecar.modules._shared import job_input

    assert http._GuardedRedirects is url_guard.GuardedRedirects
    assert job_input._GuardedRedirects is url_guard.GuardedRedirects
    assert http.url_refusal is url_guard.url_refusal
    assert job_input._url_refusal is url_guard.url_refusal
