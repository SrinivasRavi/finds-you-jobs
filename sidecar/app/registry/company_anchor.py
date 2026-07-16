"""Company anchoring — parse a job's canonical URL into the signals that resolve
its LinkedIn company entity (FR-NW-02).

Two outputs, both from the posting URL the scraper already stored:
- `employer_domain` — the employer's own website domain, when the URL host is the
  employer's site rather than an ATS host (common on Greenhouse embeds, e.g.
  `abnormal.ai/careers/…`, `careers.airbnb.com/…`). This is the strongest anchor:
  a website match against a LinkedIn company entity ⇒ silent auto-pick.
- `resolution_key` — the stable cache key for a resolved company, so every job of
  the same employer reuses one typeahead + one user choice (no re-prompting).

MIT (own code). Deliberately does NOT import the scraper adapters (framework-free
silos, §5.2) nor `voyager_py` (GPL, NFR-LIC-01) — the URL parsing is a small,
independent clean-room reimplementation.
"""

from __future__ import annotations

from urllib.parse import urlparse

# ATS / aggregator hosts whose domain is the *board provider*, never the employer.
# When the posting URL sits on one of these we have no employer domain from the
# URL (only the slug + display name) → resolution falls to name + user-confirm.
_ATS_HOSTS = frozenset({
    "boards.greenhouse.io", "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io",
    "boards-api.greenhouse.io", "boards-api.eu.greenhouse.io",
    "jobs.lever.co", "jobs.eu.lever.co", "api.lever.co", "api.eu.lever.co",
    "jobs.ashbyhq.com", "api.ashbyhq.com",
    "apply.workable.com", "jobs.workable.com",
    "remoteok.com", "remoteok.io", "remotive.com", "remotive.io",
    "news.ycombinator.com",
})


def registrable_domain(url_or_host: str | None) -> str:
    """Best-effort registrable domain from a URL or bare host, lowercased.

    `https://www.Abnormal.ai/careers` → `abnormal.ai`. Not a full public-suffix
    parse (no PSL dep) — takes the last two labels, right for the vast majority of
    employer domains; a wrong guess only ever costs a user-confirm, never a wrong
    pick. (Clean-room; mirrors voyager_py.company.registrable_domain by behaviour,
    not by import — MIT/GPL boundary.)"""
    if not url_or_host:
        return ""
    raw = url_or_host.strip().lower()
    if "//" not in raw:
        raw = "//" + raw
    host = urlparse(raw).netloc or ""
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    return ".".join(labels[-2:])


def _host(canonical_url: str) -> str:
    if not canonical_url:
        return ""
    raw = canonical_url.strip()
    if "//" not in raw:
        raw = "//" + raw
    return (urlparse(raw).netloc or "").split("@")[-1].split(":")[0].lower()


def employer_domain(canonical_url: str) -> str:
    """The employer's own website domain, or "" when the URL host is an ATS/board
    provider (no employer domain available from the URL)."""
    host = _host(canonical_url)
    if not host or host in _ATS_HOSTS:
        return ""
    return registrable_domain(host)


def ats_slug(canonical_url: str) -> str:
    """The per-employer ATS board slug from a Greenhouse/Lever/Ashby/Workable URL.

    `boards.greenhouse.io/6sense/…` → `6sense`; `jobs.lever.co/coupa/…` → `coupa`;
    `jobs.ashbyhq.com/hopper/…` → `hopper`. "" when the host isn't a known
    slug-first ATS (the slug is not reliably the LinkedIn vanity — it's only a
    cache-key/ranking hint, never used to build a company URL)."""
    host = _host(canonical_url)
    parts = [p for p in urlparse(
        canonical_url if "//" in canonical_url else "//" + canonical_url
    ).path.split("/") if p]
    if not parts:
        return ""
    if host.endswith("greenhouse.io"):
        # boards-api.greenhouse.io/v1/boards/<slug>/... else <slug>/...
        if host.startswith("boards-api.") and len(parts) >= 3 and parts[:2] == ["v1", "boards"]:
            return parts[2]
        return parts[0]
    if host.endswith("lever.co") or host.endswith("ashbyhq.com"):
        return parts[0]
    if "workable.com" in host:
        return parts[0]
    return ""


def resolution_key(canonical_url: str, source_adapter: str, company: str) -> str:
    """The stable cache key for this employer's resolved LinkedIn entity.

    Precedence mirrors anchor strength: employer domain > ATS `adapter:slug` >
    `name:<company>`. Every job of the same employer collapses to one key, so the
    resolution (and the user's confirm choice) is made once and reused."""
    domain = employer_domain(canonical_url)
    if domain:
        return f"domain:{domain}"
    slug = ats_slug(canonical_url)
    if slug and source_adapter:
        return f"{source_adapter}:{slug.lower()}"
    name = (company or "").strip().lower()
    if name:
        return f"name:{name}"
    return ""
