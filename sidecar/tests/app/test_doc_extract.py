"""Multi-format resume extraction (app/doc_extract.py).

Every branch of `extract_markdown` — text, docx, odt, pages, rtf, doc, and the
unsupported/empty error paths — over hand-assembled fixtures (no binary blobs
checked in). DOCX/ODT are stdlib ZIP+XML; the .pages fixture wraps a real
minimal PDF (reusing the PDF test's builder) so the preview-PDF path is
exercised end to end.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from sidecar.app.doc_extract import ExtractionError, extract_markdown, suffix_of
from sidecar.tests.app.test_pdf_markdown import _build_pdf, _text_ops

# ---------------------------------------------------------------------------
# fixtures builders
# ---------------------------------------------------------------------------


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _docx(body_inner: str) -> bytes:
    doc = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_inner}</w:body></w:document>"
    )
    return _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": doc.encode()})


def _p(text: str, *, style: str | None = None, bold: bool = False, num: bool = False) -> str:
    ppr = ""
    if style or num:
        ppr = "<w:pPr>"
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if num:
            ppr += "<w:numPr><w:ilvl w:val=\"0\"/></w:numPr>"
        ppr += "</w:pPr>"
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f"<w:p>{ppr}<w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>"


def _odt(body_inner: str) -> bytes:
    content = (
        '<?xml version="1.0"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:text>{body_inner}</office:text></office:body>"
        "</office:document-content>"
    )
    return _zip({"mimetype": b"application/vnd.oasis.opendocument.text",
                 "content.xml": content.encode()})


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------


def test_txt_and_md_read_through_as_utf8() -> None:
    assert extract_markdown("r.txt", b"Plain text resume") == "Plain text resume"
    assert extract_markdown("r.md", b"# Heading\n\n- bullet") == "# Heading\n\n- bullet"


def test_latin1_accents_survive_when_not_utf8() -> None:
    assert extract_markdown("r.txt", "Café résumé".encode("latin-1")) == "Café résumé"


# ---------------------------------------------------------------------------
# docx
# ---------------------------------------------------------------------------


def test_docx_maps_headings_bold_and_bullets() -> None:
    md = extract_markdown(
        "r.docx",
        _docx(
            _p("Srinivas Ravi", style="Title")
            + _p("Work Experience", style="Heading1")
            + _p("Details", style="Heading2")
            + _p("Backend engineer.", bold=True)
            + _p("Shipped the billing platform", num=True)
            + _p("plain line")
        ),
    )
    assert "# Srinivas Ravi" in md
    assert "# Work Experience" in md
    assert "## Details" in md
    assert "**Backend engineer.**" in md
    assert "- Shipped the billing platform" in md
    assert "plain line" in md


def test_docx_tab_and_break_become_whitespace() -> None:
    body = (
        "<w:p><w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t></w:r></w:p>"
    )
    md = extract_markdown("r.docx", _docx(body))
    assert "a b" in md and "c" in md


def test_docx_corrupt_zip_raises_extraction_error() -> None:
    with pytest.raises(ExtractionError):
        extract_markdown("r.docx", b"not a zip")


def test_docx_empty_body_raises_empty_error() -> None:
    with pytest.raises(ExtractionError, match="No text"):
        extract_markdown("r.docx", _docx(""))


def test_docx_with_doctype_is_refused_not_expanded() -> None:
    # A "billion laughs" / XXE-shaped part must be rejected up front, never fed
    # to the XML parser (ruff S314 hardening).
    evil = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lol [<!ENTITY lol "lol"><!ENTITY a "&lol;&lol;">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>"
    )
    blob = _zip({"word/document.xml": evil.encode()})
    with pytest.raises(ExtractionError, match="won't process for safety"):
        extract_markdown("r.docx", blob)


# ---------------------------------------------------------------------------
# odt
# ---------------------------------------------------------------------------


def test_odt_maps_headings_paragraphs_and_lists() -> None:
    body = (
        '<text:h text:outline-level="1">Summary</text:h>'
        "<text:p>Forward-deployed engineer.</text:p>"
        "<text:list><text:list-item><text:p>Owned billing</text:p></text:list-item>"
        "<text:list-item><text:p>Cut latency 40%</text:p></text:list-item></text:list>"
    )
    md = extract_markdown("r.odt", _odt(body))
    assert "# Summary" in md
    assert "Forward-deployed engineer." in md
    assert "- Owned billing" in md
    assert "- Cut latency 40%" in md


def test_odt_corrupt_raises() -> None:
    with pytest.raises(ExtractionError):
        extract_markdown("r.odt", b"nope")


# ---------------------------------------------------------------------------
# pages (embedded preview PDF)
# ---------------------------------------------------------------------------


def test_pages_reads_embedded_preview_pdf() -> None:
    # The embedded preview PDF must be found and run through the PDF path — its
    # words (with real spacing) come back. (Heading promotion is pdf_markdown's
    # own tested concern; here we only prove the preview was read.)
    pdf = _build_pdf(
        _text_ops([(72, 110, 11, "F1", "Backend engineer with six years of experience")])
    )
    md = extract_markdown("r.pages", _zip({"QuickLook/Preview.pdf": pdf}))
    assert "Backend engineer with six years of experience" in md


def test_pages_without_preview_gives_actionable_error() -> None:
    with pytest.raises(ExtractionError, match="no embedded PDF preview"):
        extract_markdown("r.pages", _zip({"Index/Document.iwa": b"\x00\x01"}))


# ---------------------------------------------------------------------------
# rtf (best-effort)
# ---------------------------------------------------------------------------


def test_rtf_strips_control_words() -> None:
    rtf = rb"{\rtf1\ansi\deff0 {\fonttbl{\f0 Arial;}}\f0\fs24 Hello \b world\b0\par Line two\par}"
    md = extract_markdown("r.rtf", rtf)
    assert "Hello" in md and "world" in md
    assert "Line two" in md
    assert "\\rtf" not in md and "fonttbl" not in md


def test_rtf_rejects_non_rtf() -> None:
    with pytest.raises(ExtractionError):
        extract_markdown("r.rtf", b"just text, no rtf header")


# ---------------------------------------------------------------------------
# doc + unsupported
# ---------------------------------------------------------------------------


def test_legacy_doc_gives_export_guidance() -> None:
    with pytest.raises(ExtractionError, match="save as PDF or .docx"):
        extract_markdown("r.doc", b"\xd0\xcf\x11\xe0stuff")  # OLE magic


def test_unsupported_type_raises() -> None:
    with pytest.raises(ExtractionError, match="Unsupported file type"):
        extract_markdown("photo.png", b"\x89PNG\r\n")


def test_empty_file_raises() -> None:
    with pytest.raises(ExtractionError, match="No text"):
        extract_markdown("r.txt", b"   \n\t ")


def test_suffix_of_is_lowercased() -> None:
    assert suffix_of("Resume.PDF") == ".pdf"
    assert suffix_of("noext") == ""
