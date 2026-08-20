"""SSRF guard (technical-audit F-M1) — the ONE copy of the fetch-layer URL
refusal logic, shared by `scraper/http.py` and `_shared/job_input.py` (they
deliberately mirrored each other until the 2026-07-25 dedup; this module is
that mirror collapsed). The browser-navigate layer refuses the same class of
target through its own guard, jobapplier's UrlPolicy (packages/jobapplier/
executor.py section 4.3); the two deliberately differ (this one resolves, that one
reads literal IPs) and are NOT kept aligned by hand — the shared case table in
`tests/modules/shared/test_url_guard_corpus.py` runs against both and pins the
divergence (duplication audit D-M10).

At the fetch layer: watchlisted/pasted URLs must not let the sidecar fetch its
own loopback services, LAN gear, or a cloud metadata endpoint
(169.254.169.254). Every URL — and every redirect hop — is checked
against the addresses its host actually resolves to, immediately before the
fetch. Documented boundary: resolution here and urllib's own resolve are two
lookups, so a DNS-rebinding TOCTOU between them remains possible; full IP
pinning isn't worth a custom transport for a localhost desktop app whose
fetches are the user's own.

Callers wrap refusals in their own typed error (ScraperError, JobInputError)
at the call site — this module only returns reasons / raises URLError on
redirect hops (urllib's contract inside an opener).
"""

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
