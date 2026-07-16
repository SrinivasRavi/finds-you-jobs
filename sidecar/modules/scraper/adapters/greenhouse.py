"""Greenhouse adapter — public board JSON API (no auth, no key).

Claims `boards.greenhouse.io/<slug>` / `job-boards.greenhouse.io/<slug>`
(+ `.eu` variants) and API-shaped URLs. One request per board:
`GET boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true`. The
`content=true` flag makes the list payload carry each posting's full JD body
(entity-encoded HTML in `content`) in the *same* request — so we get the
description free, still one list request, never per-job (maintainer decision
2026-07-07).
"""

from __future__ import annotations

import html
from urllib.parse import urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "greenhouse"

_HOSTS = {
    "boards.greenhouse.io": "",
    "job-boards.greenhouse.io": "",
    "boards.eu.greenhouse.io": "eu.",
    "job-boards.eu.greenhouse.io": "eu.",
    "boards-api.greenhouse.io": "",
    "boards-api.eu.greenhouse.io": "eu.",
}


def _slug_and_region(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host not in _HOSTS:
        return "", ""
    segments = [s for s in parts.path.split("/") if s]
    if host.startswith("boards-api."):
        # boards-api.greenhouse.io/v1/boards/<slug>/...
        if len(segments) >= 3 and segments[0] == "v1" and segments[1] == "boards":
            return segments[2], _HOSTS[host]
        return "", ""
    return (segments[0], _HOSTS[host]) if segments else ("", "")


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.type == ID and not entry.url:
        return ""
    slug, _ = _slug_and_region(entry.url) if entry.url else ("", "")
    return slug


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    slug, region = _slug_and_region(entry.url)
    if not slug:
        raise ScraperError(ID, f"cannot extract a board slug from {entry.url}")
    payload = fetcher.get_json(
        f"https://boards-api.{region}greenhouse.io/v1/boards/{slug}/jobs?content=true"
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ScraperError(ID, f"unexpected payload shape from board {slug}: no jobs[] list")

    jobs: list[NormalizedJob] = []
    for raw in payload["jobs"]:
        location = raw.get("location") or {}
        jobs.append(
            NormalizedJob(
                title=str(raw.get("title") or ""),
                canonical_url=str(raw.get("absolute_url") or ""),
                company=entry.company or str(raw.get("company_name") or "") or slug,
                location=str(location.get("name") or ""),
                description=_content_text(raw.get("content")),
                posted_at=str(raw.get("first_published") or raw.get("updated_at") or ""),
                source_adapter=ID,
            )
        )
    return jobs


def _content_text(content: object) -> str:
    """`content=true` returns entity-encoded HTML — unescape once to real HTML,
    then strip tags to plain text (the module's shared extractor)."""
    if not content:
        return ""
    return strip_html(html.unescape(str(content)))
