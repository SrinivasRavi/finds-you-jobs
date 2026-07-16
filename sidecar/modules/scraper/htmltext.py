"""Minimal HTML → text for feed payloads (RSS descriptions, HN comments).

Same approach as `_shared/job_input.py`'s extractor, kept module-local so the
scraper has no LLM-module imports. Not a sanitizer — output is data, never
rendered as markup.
"""

from __future__ import annotations

import html
import re


def strip_html(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", fragment)
    fragment = re.sub(r"(?i)<p[^>]*>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    fragment = re.sub(r"[ \t]+", " ", fragment)
    fragment = re.sub(r" ?\n ?", "\n", fragment)
    fragment = re.sub(r"\n{3,}", "\n\n", fragment)
    return fragment.strip()
