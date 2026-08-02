"""Shared private-address corpus for the two URL guards (duplication audit
D-M10).

The same class of target — loopback, LAN, link-local, cloud metadata — is
refused at two different layers by two guards that deliberately do NOT work
the same way:

- `modules/_shared/url_guard.url_refusal` (fetch layer: Scraper + job input)
  RESOLVES the host and judges the addresses it actually gets back, so a
  public-looking name pointing at 10.x dies here.
- `packages/jobapplier.UrlPolicy.check` (browser-navigate layer) judges
  LITERAL IPs only and lets an ordinary DNS name through — Playwright resolves
  inside the browser, so a resolve-then-navigate check here would be a second
  lookup that proves nothing.

That divergence is a design decision, and it used to be carried by prose in
each module pointing at the other. This file is the sync mechanism instead:
ONE case table, executed against BOTH guards, with the divergence recorded as
expectations rather than as a promise to keep two files worded alike.

Zero network — host resolution is faked exactly as `test_http_guards.py` does.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import pytest

from sidecar.modules._shared import url_guard
from sidecar.modules._shared.url_guard import url_refusal
from sidecar.packages.jobapplier.executor import UrlPolicy

_RESOLVES = {"internal.corp.example": "10.1.2.3", "boards.example.com": "93.184.216.34"}
_UNRESOLVABLE = {"no-such-host.invalid"}


@pytest.fixture(autouse=True)
def _faked_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mapped names answer from the table; anything else is treated as an IP
    literal (the `test_http_guards.py` convention). No DNS, ever."""

    def fake_getaddrinfo(host: str, port: object, *a: object, **kw: object) -> list:
        if host in _UNRESOLVABLE:
            raise socket.gaierror(8, "nodename nor servname provided")
        addr = _RESOLVES.get(host, host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))]

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", fake_getaddrinfo)


# (url, fetch guard refuses?, navigate policy refuses?, why)
CORPUS: list[tuple[str, bool, bool, str]] = [
    # Address is in the URL itself — both layers must refuse.
    ("http://169.254.169.254/latest/meta-data/", True, True, "cloud metadata (link-local)"),
    ("http://127.0.0.1:8080/secrets", True, True, "IPv4 loopback"),
    ("http://10.0.0.7/admin", True, True, "RFC1918 10/8"),
    ("http://172.16.4.2/", True, True, "RFC1918 172.16/12"),
    ("http://192.168.1.1/router", True, True, "RFC1918 192.168/16"),
    ("http://[::1]/", True, True, "IPv6 loopback"),
    ("http://[fe80::1]/", True, True, "IPv6 link-local"),
    ("http://0.0.0.0/", True, True, "unspecified address"),
    # Known-local names — refused by name, without resolving.
    ("http://localhost:8080/", True, True, "loopback by name"),
    ("http://printer.local/", True, True, "mDNS .local"),
    # Scheme / shape refusals.
    ("ftp://example.com/jobs", True, True, "scheme is not http(s)"),
    ("file:///etc/passwd", True, True, "scheme is not http(s)"),
    ("http:///no-host", True, True, "URL carries no host"),
    # Ordinary public target — both layers allow.
    ("https://boards.example.com/acme", False, False, "public board"),
    # DELIBERATE divergence: only the fetch layer resolves the name.
    ("https://internal.corp.example/jobs", True, False, "public name → 10.1.2.3"),
    ("https://no-such-host.invalid/", True, False, "name does not resolve"),
]


@pytest.mark.parametrize(
    ("url", "guard_refuses", "policy_refuses", "why"), CORPUS, ids=[c[0] for c in CORPUS]
)
def test_both_guards_match_the_corpus(
    url: str, guard_refuses: bool, policy_refuses: bool, why: str
) -> None:
    assert (url_refusal(url) is not None) is guard_refuses, f"fetch guard — {why}"
    assert (UrlPolicy().check(url) is not None) is policy_refuses, f"navigate policy — {why}"


def test_literal_address_cases_never_disagree() -> None:
    """The resolution difference may change the verdict only for NAMES. A URL
    with the address spelled out that one layer refuses and the other allows
    would be a hole, not a design choice — this is the invariant the two
    modules used to assert at each other in prose."""
    for url, _guard, _policy, why in CORPUS:
        host = urlsplit(url).hostname or ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            continue  # a name — divergence lives here, by design
        assert url_refusal(url) is not None, f"fetch guard let a literal through — {why}"
        assert UrlPolicy().check(url) is not None, f"navigate policy let a literal through — {why}"


def test_navigate_policy_allows_names_the_fetch_guard_resolves_away() -> None:
    """The divergence itself, stated once: the browser layer sees a name and
    passes it to Playwright; the fetch layer resolves it and refuses."""
    private_name = "https://internal.corp.example/jobs"
    assert UrlPolicy().check(private_name) is None
    assert "10.1.2.3" in (url_refusal(private_name) or "")
