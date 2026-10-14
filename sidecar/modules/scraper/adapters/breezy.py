"""Breezy adapter: public positions JSON API, no key.

Claims `{company}.breezy.hr`. `/json` lists positions with a nested location
(verified 2026-07-18; a flat `location.name` is still accepted). No JD and no
per-position JSON, so `fetch_detail` reads the page's description block.
career-ops's MIT provider as behavioral reference."""

from __future__ import annotations

import re

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError
from .base import detect_url, subdomain_tenant

ID = "breezy"
LABEL = "Breezy"
KIND = "ats"

_SUFFIX = ".breezy.hr"
_NOT_TENANTS = {"www", "app", "api", "help"}


def _tenant(url: str) -> str:
    return subdomain_tenant(url, _SUFFIX, _NOT_TENANTS)


_DIV_TAG_RE = re.compile(r'<div\b([^>]*)>', re.IGNORECASE)
_CLASS_RE = re.compile(r'class="([^"]*)"', re.IGNORECASE)
_OPEN_DIV_RE = re.compile(r"<div\b[^>]*>", re.IGNORECASE)
_CLOSE_DIV_RE = re.compile(r"</div\s*>", re.IGNORECASE)


def _description_block(html: str) -> str:
    """The innerHTML of the first `<div class="description">` block, walking
    nested `<div>`s to find its true close tag — a naive slice to the first
    `</div>` would cut the JD short and a naive offset slice would pull in
    surrounding chrome (breadcrumbs, apply-button i18n placeholders)."""
    for tag in _DIV_TAG_RE.finditer(html):
        classes = _CLASS_RE.search(tag.group(1))
        if classes and "description" in classes.group(1).split():
            return _balanced_inner(html, tag.end())
    return ""


def _balanced_inner(html: str, start: int) -> str:
    depth, pos = 1, start
    while True:
        next_open = _OPEN_DIV_RE.search(html, pos)
        next_close = _CLOSE_DIV_RE.search(html, pos)
        if not next_close:
            return ""  # unbalanced markup — bail rather than guess
        if next_open and next_open.start() < next_close.start():
            depth += 1
            pos = next_open.end()
            continue
        depth -= 1
        if depth == 0:
            return html[start : next_close.start()]
        pos = next_close.end()


def fetch_detail(job: NormalizedJob, fetcher: Fetcher) -> str:
    """The posting's JD lives only in the position page HTML (no per-position
    JSON endpoint) — fetch `job.canonical_url` and extract the
    `<div class="description">` block. "" when the page has no such block."""
    html = fetcher.get_text(job.canonical_url)
    return strip_html(_description_block(html))


def detect(entry: SourceEntry) -> str:
    return detect_url(entry, ID, _tenant)


def _location(raw: dict) -> str:
    location = raw.get("location")
    if not isinstance(location, dict):
        return ""
    name = str(location.get("name") or "")
    if name:
        return name
    parts: list[str] = [str(location.get("city") or "").strip()]
    for key in ("state", "country"):
        nested = location.get(key)
        if isinstance(nested, dict):
            parts.append(str(nested.get("name") or "").strip())
    return ", ".join(p for p in parts if p)


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    tenant = _tenant(entry.url)
    if not tenant:
        raise ScraperError(ID, f"cannot extract a company subdomain from {entry.url}")
    payload = fetcher.get_json(f"https://{tenant}.breezy.hr/json")
    if not isinstance(payload, list):
        got = type(payload).__name__
        raise ScraperError(ID, f"unexpected payload shape: expected a JSON list, got {got}")

    jobs: list[NormalizedJob] = []
    for raw in payload:
        if not isinstance(raw, dict) or not raw.get("url"):
            continue
        jobs.append(
            NormalizedJob(
                title=str(raw.get("name") or ""),
                canonical_url=str(raw.get("url") or ""),
                company=entry.company or tenant,
                location=_location(raw),
                posted_at=str(raw.get("published_date") or ""),
                description="",  # not in the list payload; fetch_detail fills it
                source_adapter=ID,
            )
        )
    return jobs
