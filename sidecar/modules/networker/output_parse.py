"""Parse the draft skill's output contract: ===MESSAGE=== text then ===NOTES===
bullets. Mirrors the scorer's contract parser (fence-stripping, typed errors)."""

from __future__ import annotations

import re

from .types import NetworkerError

_MESSAGE_RE = re.compile(r"===\s*MESSAGE\s*===\s*\n(.*?)\n===\s*NOTES\s*===", re.DOTALL)
_NOTES_RE = re.compile(r"===\s*NOTES\s*===\s*(.*)\Z", re.DOTALL)


def parse_output(raw: str) -> tuple[str, list[str]]:
    """Return (message_text, notes[]). Raises NetworkerError on a contract miss."""
    stripped = raw.strip()
    # Models sometimes wrap the whole contract in a markdown fence; strip one.
    fenced = re.fullmatch(r"```(?:markdown|md|text)?\s*\n(.*)\n```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)

    m_message = _MESSAGE_RE.search(stripped)
    m_notes = _NOTES_RE.search(stripped)
    if not m_message or not m_notes:
        raise NetworkerError(
            "parse",
            "draft output did not follow the ===MESSAGE===/===NOTES=== contract; "
            f"got {len(raw)} chars starting: {raw.strip()[:200]!r}",
        )

    message = m_message.group(1).strip()
    if not message:
        raise NetworkerError("parse", "MESSAGE block is empty")

    notes = [
        line.lstrip("-• ").strip()
        for line in m_notes.group(1).strip().splitlines()
        if line.strip() and line.strip() not in {"-", "•"}
    ]
    return message, notes
