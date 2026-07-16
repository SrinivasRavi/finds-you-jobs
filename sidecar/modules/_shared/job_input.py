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

_FETCH_TIMEOUT_S = 20  # matches the Add-by-URL contract (FR-JB-09)


class JobInputError(Exception):
    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        self.message = message
        super().__init__(f"[{stage}] {message}")


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
        # Scheme is constrained to http(s) by the branch condition above.
        req = urllib.request.Request(  # noqa: S310
            job, headers={"User-Agent": "findsyoujobs/0.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:  # noqa: S310
                body = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise JobInputError("job-fetch", f"could not fetch {job}: {e}") from e
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
        return p.read_text()

    if len(job.strip()) < 80:
        raise JobInputError(
            "job-input",
            f"job input is neither a URL, an existing .md/.txt file, nor JD text "
            f"(got {len(job.strip())} chars — a real JD is longer)",
        )
    return job
