"""Is a posting still open? Read off its own page (audit-2026-10-14.md 8.7).

Used for a job no complete board lists: a search hit, or a link the user added.
A 404 or 410, a redirect to a board's error page, or a page that says so is
closed. Any answer that proves neither (a 403, a 429, a timeout) is unknown and
changes nothing. Script and style text is dropped first: a page's JavaScript
carries "no longer available" strings for states it isn't in."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from .htmltext import strip_html

_CLOSED_TEXT = re.compile(
    r"no longer accepting applications"
    r"|this (?:job|position|role|posting|vacancy|opening) (?:is|has been) "
    r"(?:no longer (?:available|open|active)|closed|filled|expired|removed)"
    r"|(?:job|position|posting|vacancy) (?:has )?(?:expired|been filled|been closed)"
    r"|this job has expired"
    r"|job (?:posting )?not found"
    r"|the (?:job|position) you(?: are|'re) looking for (?:is no longer|has been|doesn'?t)"
    r"|applications (?:are|have) (?:now )?closed",
    re.IGNORECASE,
)


def page_verdict(status: int | None, final_url: str, html: str) -> str:
    """`open`, `closed` or `unknown` for 1 read of a posting's page."""
    if status in (404, 410):
        return "closed"
    if status != 200:
        return "unknown"
    # Greenhouse sends a closed posting to the board with `?error=true`.
    if parse_qs(urlsplit(final_url).query).get("error") == ["true"]:
        return "closed"
    return "closed" if _CLOSED_TEXT.search(strip_html(html)) else "open"
