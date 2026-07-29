"""Multi-format resume extraction → markdown, behind the Upload affordance
(Master resume ingest — FR-OB-04 / US-OB-02, extended 2026-07-28 from PDF-only).

One entry point, `extract_markdown(filename, data)`, dispatches by extension.
Dependency-free on purpose, matching `pdf_markdown.py`: DOCX and ODT are ZIP+XML
and are parsed with the stdlib; `.pages` reuses its own embedded preview PDF
through the PDF path; RTF is stripped heuristically. Legacy `.doc` (an OLE
binary) has no reliable in-process reader, so it fails with an honest
"export to PDF/DOCX" message (it can still be *attached* as a binary document —
that path never parses the bytes).

Raises `ExtractionError` (its message is safe to show the user) when a file
can't be read; callers map it to HTTP 422. An empty extraction is an error, not
a silently-empty draft (non-negotiable #3 — never persist garbage).
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_log = logging.getLogger("fyj.sidecar")


class ExtractionError(ValueError):
    """A file couldn't be extracted; the message is user-facing."""


# Plain-text-ish inputs read straight through as UTF-8 (they are already the
# markdown source, or close enough to it).
TEXT_SUFFIXES = frozenset({".md", ".txt", ".markdown"})

# Every suffix the extractor will *attempt* (for the caller's error messaging).
# `.doc` is listed so its dedicated, honest failure message fires instead of the
# generic "unsupported type" one.
SUPPORTED_SUFFIXES = frozenset(
    {".pdf", ".docx", ".odt", ".pages", ".rtf", ".doc"} | TEXT_SUFFIXES
)


def suffix_of(filename: str) -> str:
    return Path(filename.strip()).suffix.lower()


# ---------------------------------------------------------------------------
# PDF (moved here from api/ingest.py so `.pages` can reuse it)
# ---------------------------------------------------------------------------


def _extract_pdf(data: bytes) -> str:
    """PDF → markdown, layout-aware first, flat fallback second.

    The structured pass (pdfplumber words → `pages_to_markdown`) rebuilds
    headings/bullets/bold and — critically — inter-word spacing, which pypdf's
    flat extraction drops entirely on some generators (a Google-Docs-exported
    resume came back with the words fused together). Any structured-pass
    failure falls through to pypdf so an odd PDF still yields *something*;
    a fully unreadable file raises the honest error."""
    from .pdf_markdown import pages_to_markdown

    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [
                page.extract_words(extra_attrs=["size", "fontname"]) for page in pdf.pages
            ]
        markdown = pages_to_markdown(pages)
        if markdown.strip():
            return markdown
    except Exception:  # noqa: BLE001 — structured pass is best-effort by design
        _log.exception("structured PDF extraction failed; falling back to flat pypdf")

    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        flat_pages = [page.extract_text() or "" for page in reader.pages]
    except (PyPdfError, ValueError, OSError) as exc:
        raise ExtractionError(
            "Couldn't read that PDF (it may be encrypted or corrupted). "
            "Paste your resume text instead."
        ) from exc
    return "\n\n".join(p for p in flat_pages if p.strip())


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Résumés are sometimes latin-1/Windows-1252 (accented names); fall back
        # — but a NUL byte marks binary mislabeled as .txt, so refuse rather than
        # yield garbage (non-negotiable #3). latin-1 itself decodes any bytes.
        if b"\x00" in data:
            raise ExtractionError(
                "Couldn't decode that file as text. Upload a PDF, Word (.docx), "
                "ODT, or Pages resume — or paste your resume text instead."
            ) from None
        return data.decode("latin-1")


# ---------------------------------------------------------------------------
# Safe ZIP+XML helpers for untrusted uploads (docx/odt)
# ---------------------------------------------------------------------------

# Cap the uncompressed size of an XML member so a small zip can't decompress to
# gigabytes (a zip bomb) when we read it fully into memory.
_MAX_XML_BYTES = 40 * 1024 * 1024  # 40 MiB


def _read_zip_member(z: zipfile.ZipFile, name: str) -> bytes:
    info = z.getinfo(name)  # raises KeyError if absent (caller maps it)
    if info.file_size > _MAX_XML_BYTES:
        raise ExtractionError(
            "That file's contents are implausibly large — it may be corrupt. "
            "Export to PDF and try again."
        )
    return z.read(name)


def _parse_xml(content: bytes) -> ET.Element:
    """Parse an OOXML/ODF part, refusing DTDs/custom entities first.

    Legitimate Office/OpenDocument parts never declare a DOCTYPE or custom
    entities, so rejecting them closes the "billion laughs" / XXE class of
    attacks on untrusted uploads; ElementTree's expat resolves no external
    entities by default, and the uncompressed size is capped upstream."""
    if b"<!DOCTYPE" in content or b"<!ENTITY" in content:
        raise ExtractionError(
            "That file declares a document type we won't process for safety. "
            "Export to PDF and try again."
        )
    return ET.fromstring(content)  # noqa: S314 — DOCTYPE/ENTITY refused above


# ---------------------------------------------------------------------------
# DOCX (Office Open XML: a ZIP whose word/document.xml holds the body)
# ---------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _w_bool(rpr: ET.Element | None, name: str) -> bool:
    """A run-property boolean (w:b / w:i): present and not explicitly off."""
    if rpr is None:
        return False
    el = rpr.find(f"{_W}{name}")
    if el is None:
        return False
    return el.get(f"{_W}val") not in ("0", "false", "off")


def _docx_run_text(run: ET.Element) -> str:
    out: list[str] = []
    for child in run:
        tag = child.tag
        if tag == f"{_W}t":
            out.append(child.text or "")
        elif tag == f"{_W}tab":
            out.append(" ")
        elif tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
    return "".join(out)


def _docx_paragraph(p: ET.Element) -> str:
    ppr = p.find(f"{_W}pPr")
    style = ""
    has_num = False
    if ppr is not None:
        pstyle = ppr.find(f"{_W}pStyle")
        if pstyle is not None:
            style = (pstyle.get(f"{_W}val") or "").replace(" ", "").lower()
        has_num = ppr.find(f"{_W}numPr") is not None

    parts: list[str] = []
    for run in p.iter(f"{_W}r"):
        text = _docx_run_text(run)
        if not text:
            continue
        rpr = run.find(f"{_W}rPr")
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        core = text.strip()
        if core:
            if _w_bool(rpr, "b"):
                core = f"**{core}**"
            if _w_bool(rpr, "i"):
                core = f"*{core}*"
        parts.append(f"{lead}{core}{trail}")

    inline = "".join(parts).strip()
    if not inline:
        return ""
    if style in ("title", "heading1"):
        return f"# {inline}"
    if style == "heading2":
        return f"## {inline}"
    if style.startswith("heading"):  # heading3..9, subtitle-ish
        return f"### {inline}"
    if has_num or style.startswith("list"):
        return (f"1. {inline}" if "number" in style else f"- {inline}")
    return inline


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            body_xml = _read_zip_member(z, "word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ExtractionError(
            "Couldn't read that Word file. Re-save it as .docx or PDF and try again."
        ) from exc
    try:
        root = _parse_xml(body_xml)
    except ET.ParseError as exc:
        raise ExtractionError(
            "That Word file's contents couldn't be parsed. Export to PDF and try again."
        ) from exc

    blocks: list[str] = []
    body = root.find(f"{_W}body")
    for el in list(body) if body is not None else []:
        if el.tag == f"{_W}p":
            line = _docx_paragraph(el)
            if line:
                blocks.append(line)
        elif el.tag == f"{_W}tbl":
            # Flatten table cells to plain lines — layout tables shouldn't
            # become malformed markdown tables.
            for row in el.iter(f"{_W}tr"):
                cells = [
                    " ".join(_docx_run_text(r) for r in cell.iter(f"{_W}r")).strip()
                    for cell in row.findall(f"{_W}tc")
                ]
                line = " · ".join(c for c in cells if c)
                if line:
                    blocks.append(line)
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# ODT (OpenDocument Text: a ZIP whose content.xml holds office:text)
# ---------------------------------------------------------------------------

_ODF_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_ODF_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


def _odt_inline(el: ET.Element) -> str:
    """All text under a paragraph/heading, in order (spans flattened; ODT bold
    lives in referenced automatic styles — extraction stays plain text)."""
    return "".join(el.itertext()).strip()


def _odt_walk(container: ET.Element, blocks: list[str], list_depth: int = 0) -> None:
    for el in container:
        tag = el.tag
        if tag == f"{{{_ODF_TEXT}}}h":
            text = _odt_inline(el)
            if not text:
                continue
            level = el.get(f"{{{_ODF_TEXT}}}outline-level", "1")
            hashes = "#" * min(max(int(level) if level.isdigit() else 1, 1), 3)
            blocks.append(f"{hashes} {text}")
        elif tag == f"{{{_ODF_TEXT}}}p":
            text = _odt_inline(el)
            if text:
                blocks.append(f"- {text}" if list_depth > 0 else text)
        elif tag == f"{{{_ODF_TEXT}}}list":
            for item in el.findall(f"{{{_ODF_TEXT}}}list-item"):
                _odt_walk(item, blocks, list_depth + 1)


def _extract_odt(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            content = _read_zip_member(z, "content.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ExtractionError(
            "Couldn't read that ODT file. Re-save it or export to PDF and try again."
        ) from exc
    try:
        root = _parse_xml(content)
    except ET.ParseError as exc:
        raise ExtractionError(
            "That ODT file's contents couldn't be parsed. Export to PDF and try again."
        ) from exc

    blocks: list[str] = []
    body = root.find(f"{{{_ODF_OFFICE}}}body")
    office_text = body.find(f"{{{_ODF_OFFICE}}}text") if body is not None else None
    if office_text is not None:
        _odt_walk(office_text, blocks)
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Apple Pages (a ZIP bundle; modern files embed a preview PDF)
# ---------------------------------------------------------------------------

_PAGES_PREVIEWS = ("preview.pdf", "QuickLook/Preview.pdf")


def _extract_pages(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            for cand in _PAGES_PREVIEWS:
                if cand in names:
                    return _extract_pdf(z.read(cand))
    except zipfile.BadZipFile as exc:
        raise ExtractionError(
            "Couldn't read that Pages file. In Pages, export to PDF or Word (.docx) "
            "and upload that."
        ) from exc
    raise ExtractionError(
        "That Pages file has no embedded PDF preview to read. In Pages, export to "
        "PDF or Word (.docx) and upload that."
    )


# ---------------------------------------------------------------------------
# RTF (best-effort de-RTF — "great to have", per the format scope)
# ---------------------------------------------------------------------------

_RTF_GROUP_SKIP = re.compile(r"\\\*\\[a-z]+.*?(?<!\\)}", re.DOTALL)
_RTF_HEX = re.compile(r"\\'([0-9a-fA-F]{2})")
_RTF_CONTROL = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?")


def _extract_rtf(data: bytes) -> str:
    text = data.decode("latin-1", errors="replace")
    if "\\rtf" not in text[:64]:
        raise ExtractionError(
            "That doesn't look like a valid RTF file. Export to PDF and try again."
        )
    # Drop destination groups we never want as text (fonts, colors, styles).
    text = _RTF_GROUP_SKIP.sub("", text)
    # Paragraph / line breaks and tabs → real whitespace before stripping codes.
    text = re.sub(r"\\par[d]?\b", "\n", text)
    text = re.sub(r"\\line\b", "\n", text)
    text = text.replace("\\tab", " ")
    # \'xx hex escapes → the latin-1 char.
    text = _RTF_HEX.sub(lambda m: bytes([int(m.group(1), 16)]).decode("latin-1"), text)
    # Remaining control words, then braces.
    text = _RTF_CONTROL.sub("", text)
    text = text.replace("{", "").replace("}", "")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------------------
# Legacy .doc — no reliable in-process reader
# ---------------------------------------------------------------------------


def _extract_doc(_data: bytes) -> str:
    raise ExtractionError(
        "Legacy Word (.doc) can't be read directly. In Word, save as PDF or .docx "
        "and upload that (you can still attach the .doc file itself to an application)."
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".odt": _extract_odt,
    ".pages": _extract_pages,
    ".rtf": _extract_rtf,
    ".doc": _extract_doc,
}


def extract_markdown(filename: str, data: bytes) -> str:
    """Extract `data` (a `filename`-named document) to markdown/plain text.

    Raises `ExtractionError` on an unsupported type, an unreadable file, or an
    empty result — never returns an empty/garbage draft."""
    suffix = suffix_of(filename)
    if suffix in TEXT_SUFFIXES:
        text = _decode_text(data)
    elif suffix in _EXTRACTORS:
        text = _EXTRACTORS[suffix](data)
    else:
        raise ExtractionError(
            f"Unsupported file type {suffix or '(none)'!r}. Upload a PDF, Word "
            "(.docx), ODT, Pages, or text resume — or paste your resume text instead."
        )
    text = (text or "").strip()
    if not text:
        raise ExtractionError(
            "No text could be extracted from that file (an image-only PDF or scan "
            "has no selectable text). Paste your resume text instead."
        )
    return text
