"""_shared/job_input URL-fetch guards (technical-audit F-M1 SSRF, F-L8 body
cap) — zero network: DNS resolution and the urllib opener are both faked.
The text/file input paths are covered by the scorer/tailorer module tests.
"""

from __future__ import annotations

import io
import socket
import urllib.error
import urllib.request
from http.client import HTTPMessage

import pytest

from sidecar.modules._shared import job_input, url_guard
from sidecar.modules._shared.job_input import JobInputError, resolve_job


def _resolving_to(mapping: dict[str, str]):
    """A getaddrinfo fake: hostname → one address (IP literals echo back)."""

    def fake_getaddrinfo(host: str, port: object, *args: object, **kw: object) -> list:
        addr = mapping.get(host, host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))]

    return fake_getaddrinfo


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeOpener:
    def __init__(self, body: bytes) -> None:
        self.calls = 0
        self._body = body

    def open(self, req: urllib.request.Request, timeout: object = None) -> _FakeResponse:
        self.calls += 1
        return _FakeResponse(self._body)


_JD_HTML = "<main><p>" + "Build the backend platform. " * 20 + "</p></main>"


# --- F-M1: SSRF guard -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/secrets",
        "http://10.0.0.7/admin",
        "http://[::1]/",
        "http://localhost:1420/",
    ],
)
def test_private_targets_refused_before_any_request(monkeypatch, url) -> None:
    opener = _FakeOpener(_JD_HTML.encode())
    monkeypatch.setattr(job_input, "_opener", opener)
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _resolving_to({}))
    with pytest.raises(JobInputError, match=r"\[job-fetch\] refusing to fetch"):
        resolve_job(url)
    assert opener.calls == 0


def test_hostname_resolving_to_private_address_refused(monkeypatch) -> None:
    opener = _FakeOpener(_JD_HTML.encode())
    monkeypatch.setattr(job_input, "_opener", opener)
    monkeypatch.setattr(
        url_guard.socket, "getaddrinfo", _resolving_to({"internal.corp.example": "192.168.0.10"})
    )
    with pytest.raises(JobInputError, match="non-public"):
        resolve_job("https://internal.corp.example/jd")
    assert opener.calls == 0


def test_public_url_fetches_and_extracts_text(monkeypatch) -> None:
    monkeypatch.setattr(job_input, "_opener", _FakeOpener(_JD_HTML.encode()))
    monkeypatch.setattr(
        url_guard.socket, "getaddrinfo", _resolving_to({"jobs.example.com": "142.250.183.10"})
    )
    text = resolve_job("https://jobs.example.com/backend-engineer")
    assert "Build the backend platform." in text
    assert "<" not in text


def test_redirect_to_private_address_refused(monkeypatch) -> None:
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _resolving_to({}))
    handler = job_input._GuardedRedirects()
    req = urllib.request.Request("https://jobs.example.com/jd")
    with pytest.raises(urllib.error.URLError, match="refusing redirect"):
        handler.redirect_request(
            req, io.BytesIO(b""), 302, "Found", HTTPMessage(), "http://127.0.0.1:8000/steal"
        )


def test_redirect_public_to_public_allowed(monkeypatch) -> None:
    monkeypatch.setattr(
        url_guard.socket,
        "getaddrinfo",
        _resolving_to(
            {"jobs.example.com": "142.250.183.10", "boards.example.org": "142.250.183.11"}
        ),
    )
    handler = job_input._GuardedRedirects()
    req = urllib.request.Request("https://jobs.example.com/jd")
    new_req = handler.redirect_request(
        req, io.BytesIO(b""), 302, "Found", HTTPMessage(), "https://boards.example.org/jd/42"
    )
    assert new_req is not None
    assert new_req.full_url == "https://boards.example.org/jd/42"


# --- F-L8: body cap ---------------------------------------------------------


def test_body_over_cap_refused(monkeypatch) -> None:
    monkeypatch.setattr(
        job_input, "_opener", _FakeOpener(b"x" * (job_input._MAX_BYTES + 1))
    )
    monkeypatch.setattr(
        url_guard.socket, "getaddrinfo", _resolving_to({"big.example.com": "142.250.183.12"})
    )
    with pytest.raises(JobInputError, match="more than"):
        resolve_job("https://big.example.com/huge")
