"""Resume ingestion for onboarding (FR-OB-04 / US-OB-02).

A dedicated router (kept out of the concurrently-edited `routes.py`, wired via
`include_router` in `main.py` exactly like `api/engines.py`). It backs the
onboarding Resume step's upload affordance:

- `POST /api/profile/ingest` — takes a multipart file and returns the extracted
  text for the user to review/edit before it is persisted. `.md` / `.txt` are
  read as UTF-8 as-is; `.pdf` is extracted via `pypdf` (BSD-3-Clause). The
  reviewed text is persisted separately by `POST /api/profile`
  (`ProfileUpsert.resume_markdown`) — this endpoint never writes to the DB.

**Honest failure (non-negotiable #3 — never persist garbage).** An empty or
undecodable extraction returns **422** with a clear message telling the user to
paste their resume instead, rather than yielding an empty/garbled draft.
"""

from __future__ import annotations

import asyncio
import io

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter()

# Upload guard: resumes are small documents. Cap the read so a stray large/binary
# upload can't balloon memory (the extractor runs fully in-process).
_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
_TEXT_SUFFIXES = (".md", ".txt", ".markdown")
_PDF_SUFFIXES = (".pdf",)


class ProfileIngestResult(BaseModel):
    """Extracted resume text held in the wizard draft for review (not persisted)."""

    text: str
    filename: str
    chars: int


def _suffix(filename: str) -> str:
    name = filename.lower().strip()
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _extract_pdf(data: bytes) -> str:
    """Extract text from a PDF via pypdf. Raises on an unreadable/encrypted file."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PyPdfError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "Couldn't read that PDF (it may be encrypted or corrupted). "
                "Paste your resume text instead."
            ),
        ) from exc
    return "\n\n".join(p for p in pages if p.strip())


@router.post("/api/profile/ingest")
async def ingest_resume(file: UploadFile = File(...)) -> ProfileIngestResult:  # noqa: B008
    filename = file.filename or "resume"
    suffix = _suffix(filename)

    data = await file.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That file is too large (max 10 MB). Paste your resume text instead.",
        )
    if not data:
        raise HTTPException(
            status_code=422,
            detail="That file is empty. Paste your resume text instead.",
        )

    if suffix in _TEXT_SUFFIXES:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Couldn't decode that file as text. Upload a .md/.txt/.pdf "
                    "resume, or paste your resume text instead."
                ),
            ) from exc
    elif suffix in _PDF_SUFFIXES:
        # Off the event loop (async-first rule, 2026-07-22 audit): pypdf parse
        # of a 10 MiB resume is seconds of CPU — on the loop that starves
        # /healthz and gets the sidecar kill-restarted mid-onboarding.
        text = await asyncio.to_thread(_extract_pdf, data)
    else:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported file type {suffix or '(none)'!r}. Upload a .md, .txt, "
                "or .pdf resume, or paste your resume text instead."
            ),
        )

    text = text.strip()
    if not text:
        raise HTTPException(
            status_code=422,
            detail=(
                "No text could be extracted from that file (an image-only PDF has no "
                "selectable text). Paste your resume text instead."
            ),
        )

    return ProfileIngestResult(text=text, filename=filename, chars=len(text))
