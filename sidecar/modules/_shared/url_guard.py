"""SSRF guard (F-M1): the 1 copy of the fetch-layer URL refusal, shared by
`scraper/http.py` and `_shared/job_input.py`.

Pasted or watched URLs must not reach loopback, LAN or cloud metadata
(169.254.169.254): every URL and redirect hop is checked against the addresses
its host resolves to. A DNS-rebinding window between 2 lookups remains, accepted
for a desktop app. jobapplier's UrlPolicy differs on purpose; the case table in
`tests/modules/shared/test_url_guard_corpus.py` pins both (D-M10)."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit


def _host_refusal(host: str) -> str | None:
    """None when `host` looks publicly routable, else the refusal reason."""
    if host == "localhost" or host.endswith(".local"):
        return "loopback/.local host"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        return f"cannot resolve host: {e}"
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            return f"resolves to non-public address {ip}"
    return None


def url_refusal(url: str) -> str | None:
    """None when `url` may be fetched, else the refusal reason (F-M1)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"scheme {parts.scheme!r} is not allowed"
    host = parts.hostname or ""
    if not host:
        return "URL has no host"
    return _host_refusal(host)


class GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect hop — a public URL 302-ing to a private or
    metadata address dies here, never fetched. Legit public→public redirects
    (board URL moves, host renames) pass through untouched."""

    def redirect_request(  # type: ignore[override]  # noqa: PLR0913 — stdlib signature
        self, req, fp, code, msg, headers, newurl  # noqa: ANN001
    ):
        target = urljoin(req.full_url, newurl)
        refusal = url_refusal(target)
        if refusal:
            raise urllib.error.URLError(f"refusing redirect to {target}: {refusal}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
