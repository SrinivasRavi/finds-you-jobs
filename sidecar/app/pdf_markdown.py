"""Layout-aware PDF → markdown reconstruction (resume ingest, FR-OB-04).

pypdf's flat ``extract_text()`` loses what a resume's layout actually says —
and on some generators (Google-Docs exports notably) it even drops the spaces
between words. This module takes pdfplumber's *word* extraction (each word with
its position, font size, and font name) and rebuilds the document:

- words cluster into visual lines by vertical position and join with single
  spaces — inter-word spacing survives by construction;
- the largest-font line(s) become ``#`` (the name), noticeably-larger or
  bold-and-larger lines become ``##`` (section headers);
- bullet glyphs (● • ▪ ◦ ‣ ○) start ``- `` list items, and a wrapped bullet's
  continuation line merges back into its item;
- a fully-bold body line (role · company · dates rows) is kept bold;
- consecutive plain lines with normal line spacing merge into one paragraph;
  larger vertical gaps become paragraph breaks.

The output is *best-effort structure*, not a pixel-perfect conversion — the
user reviews and edits the result in the resume editor before saving (the
ingest endpoint never persists). The heuristics are pure functions over plain
word dicts so the whole pass is unit-testable without a PDF in sight
(``sidecar/tests/app/test_pdf_markdown.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Line classification thresholds, relative to the document's body font size.
H1_RATIO = 1.45  # ≥ 1.45× body → "# " (the name on a resume)
H2_RATIO = 1.12  # ≥ 1.12× body → "## " (section headers)
BOLD_FRACTION = 0.75  # ≥ 75% of a line's characters bold → a bold line
# Vertical gap (relative to body size) above which two lines are separate
# blocks; below it a plain line continues the previous paragraph/bullet.
GAP_BLOCK_RATIO = 1.6
# A page with fewer words than this is likely image-only — not worth a guess.
MIN_WORDS = 15

BULLET_GLYPHS = "●•▪◦‣○"


@dataclass
class _Line:
    text: str
    size: float  # dominant (max) font size on the line
    top: float
    bold_fraction: float


def _is_bold(fontname: str) -> bool:
    return "bold" in fontname.lower()


def lines_from_words(words: list[dict[str, Any]]) -> list[_Line]:
    """Cluster pdfplumber words into visual lines (top-tolerance grouping),
    left-to-right within a line, single-space joined."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [ordered[0]]
    current_top = float(ordered[0]["top"])
    for word in ordered[1:]:
        size = float(word.get("size") or 10.0)
        tolerance = max(2.0, 0.35 * size)
        if float(word["top"]) - current_top > tolerance:
            lines.append(current)
            current = [word]
            current_top = float(word["top"])
        else:
            current.append(word)
    lines.append(current)

    out: list[_Line] = []
    for line_words in lines:
        line_words.sort(key=lambda w: float(w["x0"]))
        text = " ".join(str(w["text"]) for w in line_words).strip()
        if not text:
            continue
        chars = sum(len(str(w["text"])) for w in line_words)
        bold_chars = sum(
            len(str(w["text"])) for w in line_words if _is_bold(str(w.get("fontname", "")))
        )
        out.append(
            _Line(
                text=text,
                size=max(float(w.get("size") or 10.0) for w in line_words),
                top=min(float(w["top"]) for w in line_words),
                bold_fraction=(bold_chars / chars) if chars else 0.0,
            )
        )
    return out


def _body_size(pages: list[list[dict[str, Any]]]) -> float:
    """The document's body font size: the size carrying the most characters."""
    weight: dict[float, int] = {}
    for words in pages:
        for w in words:
            size = round(float(w.get("size") or 10.0), 1)
            weight[size] = weight.get(size, 0) + len(str(w["text"]))
    if not weight:
        return 10.0
    return max(weight.items(), key=lambda kv: kv[1])[0]


def _strip_bullet(text: str) -> str | None:
    """The bullet-item text if `text` starts with a bullet glyph, else None."""
    if text and text[0] in BULLET_GLYPHS:
        return text[1:].lstrip() or None
    return None


def pages_to_markdown(pages: list[list[dict[str, Any]]]) -> str:
    """Rebuild markdown from per-page pdfplumber word lists. Returns "" when
    there is too little text to structure (caller falls back / errors)."""
    total_words = sum(len(p) for p in pages)
    if total_words < MIN_WORDS:
        return ""
    body = _body_size(pages)

    # (kind, text) blocks; kind ∈ h1 | h2 | bullet | bold | body
    blocks: list[list[str]] = []  # [kind, text]
    prev_line: _Line | None = None

    for words in pages:
        prev_line = None  # a page break is always a block break
        for line in lines_from_words(words):
            bullet_text = _strip_bullet(line.text)
            if line.size >= body * H1_RATIO:
                kind, text = "h1", line.text
            elif line.size >= body * H2_RATIO:
                kind, text = "h2", line.text
            elif bullet_text is not None:
                kind, text = "bullet", bullet_text
            elif line.bold_fraction >= BOLD_FRACTION:
                kind, text = "bold", line.text
            else:
                kind, text = "body", line.text

            # A plain line at normal line spacing continues the previous
            # bullet (wrapped item) or paragraph.
            if (
                kind == "body"
                and blocks
                and blocks[-1][0] in ("bullet", "body")
                and prev_line is not None
                and (line.top - prev_line.top) <= GAP_BLOCK_RATIO * body
            ):
                blocks[-1][1] += f" {text}"
            else:
                blocks.append([kind, text])
            prev_line = line

    rendered: list[str] = []
    for kind, text in blocks:
        if kind == "h1":
            rendered.append(f"# {text}")
        elif kind == "h2":
            rendered.append(f"## {text}")
        elif kind == "bullet":
            rendered.append(f"- {text}")
        elif kind == "bold":
            rendered.append(f"**{text}**")
        else:
            rendered.append(text)

    # Adjacent bullets form one list block (no blank line between items).
    out: list[str] = []
    for block in rendered:
        if out and block.startswith("- ") and out[-1].startswith("- "):
            out[-1] += f"\n{block}"
        else:
            out.append(block)
    return "\n\n".join(out)
