"""PDF → markdown reconstruction (app/pdf_markdown.py + the ingest wiring).

Two layers:
- pure heuristics over synthetic pdfplumber-shaped word dicts (no PDF needed):
  line clustering, heading/bullet/bold classification, wrapped-bullet and
  paragraph merging, page breaks;
- an integration pass through ``_extract_pdf`` on a minimal hand-assembled PDF,
  pinning the original bug: flat extraction fused words together
  ("Iamabackendsoftwareengineer…"); the structured pass must keep real spaces.
"""

from __future__ import annotations

from typing import Any

from sidecar.app.pdf_markdown import lines_from_words, pages_to_markdown


def w(
    text: str, x0: float, top: float, size: float = 11.0, font: str = "Helvetica"
) -> dict[str, Any]:
    return {"text": text, "x0": x0, "top": top, "size": size, "fontname": font}


def line(text: str, top: float, size: float = 11.0, font: str = "Helvetica", x0: float = 40.0):
    words = []
    x = x0
    for token in text.split(" "):
        words.append(w(token, x, top, size, font))
        x += 6.0 * size * 0.1 * len(token) + 8
    return words


# ---------------------------------------------------------------------------
# lines_from_words
# ---------------------------------------------------------------------------


def test_words_cluster_into_lines_left_to_right_with_spaces() -> None:
    words = [
        w("engineer", 200, 100),
        w("backend", 100, 100.8),  # same visual line, slight top jitter
        w("a", 60, 99.5),
        w("next", 40, 130),  # a different line
    ]
    lines = lines_from_words(words)
    assert [ln.text for ln in lines] == ["a backend engineer", "next"]


def test_line_carries_max_size_and_bold_fraction() -> None:
    words = [
        w("Senior", 40, 50, 11, "Arial-BoldMT"),
        w("Staff", 90, 50, 11, "Arial-BoldMT"),
        w("(hybrid)", 140, 50, 11, "ArialMT"),
    ]
    (ln,) = lines_from_words(words)
    assert ln.size == 11
    assert 0.5 < ln.bold_fraction < 1.0


# ---------------------------------------------------------------------------
# pages_to_markdown — classification and merging
# ---------------------------------------------------------------------------


def _resume_words() -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    words += line("Srinivas Ravi", 40, 26, "Helvetica-Bold")
    words += line("I am a backend software engineer with 6 years of experience.", 90)
    words += line("Work Experience", 130, 14, "Helvetica-Bold")
    words += line(
        "Senior Member of Technical Staff Salesforce Apr 2022–Present", 160, 11, "Helvetica-Bold"
    )
    words += line("Tableau Online Identity and Authentication team Seattle, USA", 175)
    words += line("● Working on Spring Security framework for the team", 200)
    words += line("● Designed metrics and alerting architecture using", 215)
    words += line("internal tools with a 40% cost reduction", 228)  # wrapped bullet
    words += line("Education and Certifications", 280, 14, "Helvetica-Bold")
    return words


def test_resume_structure_reconstructs() -> None:
    md = pages_to_markdown([_resume_words()])
    assert md.startswith("# Srinivas Ravi")
    assert "## Work Experience" in md
    assert "## Education and Certifications" in md
    # The fully-bold role row stays bold.
    assert "**Senior Member of Technical Staff Salesforce Apr 2022–Present**" in md
    # Bullets become one list block; the wrapped second bullet merged its
    # continuation line back into the item.
    assert (
        "- Working on Spring Security framework for the team\n"
        "- Designed metrics and alerting architecture using internal tools "
        "with a 40% cost reduction"
    ) in md
    # Spacing survived everywhere — the fused-words bug never returns.
    assert "I am a backend software engineer" in md


def test_close_plain_lines_merge_into_one_paragraph_and_gaps_split() -> None:
    words = (
        line("This paragraph wraps across two", 100)
        + line("visual lines in the PDF.", 113)  # gap 13 < 1.6 × 11
        + line("A new paragraph after a real gap.", 160)  # gap 47 → break
    )
    # Pad with body words so the size histogram is meaningful.
    words += line("filler words to establish the body font size baseline here", 300)
    md = pages_to_markdown([words])
    assert "This paragraph wraps across two visual lines in the PDF." in md
    assert "\n\nA new paragraph after a real gap." in md


def test_page_break_is_a_block_break() -> None:
    page1 = line("End of page one text here with several words present", 700)
    page2 = line("Start of page two", 40) + line(
        "with enough following words to clear the minimum-word guard", 60
    )
    md = pages_to_markdown([page1, page2])
    assert "End of page one text here with several words present\n\nStart of page two" in md


def test_too_little_text_returns_empty() -> None:
    assert pages_to_markdown([line("Only a few words", 40)]) == ""
    assert pages_to_markdown([[]]) == ""


def test_en_dash_is_not_a_bullet() -> None:
    words = line("Apr 2022–Present dates line stays plain text here today", 40)
    words += line("more filler body words for the histogram baseline values", 80)
    md = pages_to_markdown([words])
    assert "- Apr" not in md
    assert "Apr 2022–Present" in md


# ---------------------------------------------------------------------------
# integration: a real (hand-assembled) PDF through _extract_pdf
# ---------------------------------------------------------------------------


def _build_pdf(content: bytes) -> bytes:
    """A minimal valid one-page PDF: Helvetica (F1) + Helvetica-Bold (F2),
    WinAnsi encoding (so \\225 is the • bullet glyph)."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref,
    )
    return bytes(out)


def _text_ops(lines: list[tuple[float, float, float, str, str]]) -> bytes:
    """One Tj PER WORD (the layout style whose flat extraction fuses words)."""
    ops: list[bytes] = []
    for x, y_top, size, font, text in lines:
        y = 792 - y_top
        wx = x
        for token in text.split(" "):
            escaped = token.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            ops.append(
                b"BT /%s %.1f Tf %.1f %.1f Td (%s) Tj ET"
                % (font.encode(), size, wx, y, escaped.encode("latin-1"))
            )
            # Generous advance: an under-estimate overlaps the real glyph widths
            # and pdfplumber then (correctly) merges the touching words — which
            # would fake the very fusion bug this test guards against.
            wx += 0.8 * size * len(token) + 6
    return b"\n".join(ops)


def test_extract_pdf_reconstructs_structure_with_real_spacing() -> None:
    from sidecar.app.doc_extract import _extract_pdf

    content = _text_ops(
        [
            (200, 60, 24, "F2", "Srinivas Ravi"),
            (72, 110, 11, "F1", "I am a backend software engineer with six years of experience"),
            (72, 150, 14, "F2", "Work Experience"),
            (72, 180, 11, "F1", "\x95 Improved efficiency of storage host balancing by half"),
            (72, 200, 11, "F1", "\x95 Developed a failover system for a dynamic config service"),
        ]
    )
    md = _extract_pdf(_build_pdf(content))
    assert "# Srinivas Ravi" in md
    assert "## Work Experience" in md
    assert "- Improved efficiency of storage host balancing by half" in md
    assert "- Developed a failover system for a dynamic config service" in md
    # THE regression: flat extraction of this exact layout fuses words
    # ("Iamabackendsoftwareengineer…"). The structured pass must not.
    assert "I am a backend software engineer" in md
    assert "Iamabackend" not in md
