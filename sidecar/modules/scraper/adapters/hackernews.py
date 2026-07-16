"""Hacker News "Who is hiring?" adapter — HN Algolia public API (no key).

Claims `board = "hn" | "hackernews"` and `news.ycombinator.com` URLs; the
source key is always `whoishiring`. Two requests max: discover the newest
monthly "Who is hiring?" story (or use the `item?id=<N>` the URL pinned), then
`GET search_by_date?tags=comment,story_<id>` for its top-level comments. Each
top-level comment is one posting; the canonical `| Company | Role | Location |`
first line is parsed best-effort — malformed posts get low trust downstream,
we do not over-filter here (rank-don't-gate).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import Fetcher
from ..types import NormalizedJob, ScraperError

ID = "hackernews"
_CLAIM = "whoishiring"
_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
_STORY_QUERY = f"{_SEARCH}?tags=story,author_whoishiring&query=who%20is%20hiring"


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.board in {"hackernews", "hn"}:
        return _CLAIM
    host = urlsplit(entry.url).netloc.lower() if entry.url else ""
    return _CLAIM if host == "news.ycombinator.com" else ""


def _pinned_story_id(url: str) -> str:
    if not url:
        return ""
    ids = parse_qs(urlsplit(url).query).get("id")
    return ids[0] if ids else ""


def _discover_story_id(fetcher: Fetcher) -> str:
    payload = fetcher.get_json(_STORY_QUERY)
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
        raise ScraperError(ID, "unexpected story-search payload: no hits[] list")
    # hits are date-desc already; take the newest matching title.
    for hit in payload["hits"]:
        if isinstance(hit, dict) and "who is hiring" in str(hit.get("title") or "").lower():
            object_id = str(hit.get("objectID") or "")
            if object_id:
                return object_id
    raise ScraperError(ID, "no 'who is hiring' story found in search results")


def _parse_first_line(text: str) -> tuple[str, str, str]:
    first = text.split("\n", 1)[0]
    segments = [s.strip() for s in first.split("|")]
    if len(segments) >= 2:
        location = segments[2] if len(segments) >= 3 else ""
        return segments[0], segments[1], location
    return "", first[:120], ""


def fetch(entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
    story_id = _pinned_story_id(entry.url) or _discover_story_id(fetcher)
    comments_url = f"{_SEARCH}?tags=comment,story_{story_id}&hitsPerPage=1000"
    payload = fetcher.get_json(comments_url)
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
        raise ScraperError(ID, f"unexpected comments payload for story {story_id}: no hits[] list")

    jobs: list[NormalizedJob] = []
    for hit in payload["hits"]:
        if not isinstance(hit, dict):
            continue
        if str(hit.get("parent_id") or "") != str(story_id):  # top-level comments only
            continue
        text = strip_html(str(hit.get("comment_text") or ""))
        if not text:
            continue
        company, title, location = _parse_first_line(text)
        jobs.append(
            NormalizedJob(
                title=title,
                canonical_url=f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                company=company,
                location=location,
                posted_at=str(hit.get("created_at") or ""),
                description=text,
                salary="",
                source_adapter=ID,
            )
        )
    return jobs
