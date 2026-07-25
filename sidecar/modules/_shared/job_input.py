"""Resolve a job input — markdown text, a local file path, or a URL — shared by
module silos (extracted from the Tailorer at the second consumer, the Scorer,
per the M1 playbook).

Mirrors career-ops's "text or URL" input rule. URL fetching here is the
module-local minimum (stdlib only); the real fetching/normalization lives in the
Scraper module (Track M3) and replaces this seam at integration.

Modules wrap `JobInputError` into their own typed error (TailorError,
ScoreError, ...) preserving the stage + verbatim message.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from pathlib import Path

from .url_guard import GuardedRedirects as _GuardedRedirects
from .url_guard import url_refusal as _url_refusal

_FETCH_TIMEOUT_S = 20  # matches the Add-by-URL contract (FR-JB-09)
_MAX_BYTES = 20 * 1024 * 1024  # same cap as scraper/http.py — refuse absurd pages


class JobInputError(Exception):
    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        self.message = message
        super().__init__(f"[{stage}] {message}")


# SSRF guard (F-M1) — the refusal logic + guarded redirect handler live in
# `_shared/url_guard.py` (one copy, shared with `scraper/http.py` since the
# 2026-07-25 dedup; see that module's docstring for the design and the
# documented DNS-rebinding TOCTOU boundary). Refusals here are wrapped in this
# module's own typed JobInputError at the call site below.

_opener = urllib.request.build_opener(_GuardedRedirects)


def _html_to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>", "\n", page)
    page = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", page)
    page = re.sub(r"<[^>]+>", " ", page)
    page = html.unescape(page)
    page = re.sub(r"[ \t]+", " ", page)
    page = re.sub(r"\n{3,}", "\n\n", page)
    return page.strip()


def resolve_job(job: str) -> str:
    """Return the JD as markdown/plain text.

    `job` may be: raw JD text, a path to a .md/.txt file, or an http(s) URL.
    """
    if job.startswith(("http://", "https://")):
        refusal = _url_refusal(job)  # SSRF guard (F-M1) — resolved-IP check
        if refusal:
            raise JobInputError("job-fetch", f"refusing to fetch {job}: {refusal}")
        # Scheme is constrained to http(s) by the branch condition above.
        req = urllib.request.Request(  # noqa: S310
            job, headers={"User-Agent": "findsyoujobs/0.0"}
        )
        try:
            with _opener.open(req, timeout=_FETCH_TIMEOUT_S) as resp:  # noqa: S310
                raw = resp.read(_MAX_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise JobInputError("job-fetch", f"could not fetch {job}: {e}") from e
        if len(raw) > _MAX_BYTES:  # same cap + style as scraper/http.py (F-L8)
            raise JobInputError(
                "job-fetch", f"{job} returned more than {_MAX_BYTES} bytes; refusing"
            )
        body = raw.decode("utf-8", errors="replace")
        text = _html_to_text(body)
        if len(text) < 200:
            raise JobInputError(
                "job-fetch",
                f"fetched {job} but extracted only {len(text)} chars — "
                "likely a JS-only page; pass the JD text or a file instead",
            )
        return text

    p = Path(job)
    if p.suffix.lower() in {".md", ".txt"} and p.exists():
        return p.read_text(encoding="utf-8")

    if len(job.strip()) < 80:
        raise JobInputError(
            "job-input",
            f"job input is neither a URL, an existing .md/.txt file, nor JD text "
            f"(got {len(job.strip())} chars — a real JD is longer)",
        )
    return job
