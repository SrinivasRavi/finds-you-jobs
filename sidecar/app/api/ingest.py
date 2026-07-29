"""Resume ingestion for onboarding (FR-OB-04 / US-OB-02).

A dedicated router (kept out of the concurrently-edited `routes.py`, wired via
`include_router` in `main.py` exactly like `api/engines.py`). It backs the
onboarding Resume step's upload affordance:

- `POST /api/profile/ingest` — takes a multipart file and returns the extracted
  text for the user to review/edit before it is persisted. Format handling lives
  in `app/doc_extract.py` (`.md`/`.txt` UTF-8 as-is; `.pdf` via the layout-aware
  `app/pdf_markdown.py` reconstruction; `.docx`/`.odt` via stdlib ZIP+XML;
  `.pages` via its embedded preview PDF; `.rtf` best-effort). The reviewed text
  is persisted separately by `POST /api/profile`
  (`ProfileUpsert.resume_markdown`) — this endpoint never writes to the DB.

**Honest failure (non-negotiable #3 — never persist garbage).** An empty or
undecodable extraction returns **422** with a clear message telling the user to
paste their resume instead, rather than yielding an empty/garbled draft.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..doc_extract import ExtractionError, extract_markdown

router = APIRouter()

# Upload guard: resumes are small documents. Cap the read so a stray large/binary
# upload can't balloon memory (the extractor runs fully in-process).
_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB


class ProfileIngestResult(BaseModel):
    """Extracted resume text held in the wizard draft for review (not persisted)."""

    text: str
    filename: str
    chars: int


@router.post("/api/profile/ingest")
async def ingest_resume(file: UploadFile = File(...)) -> ProfileIngestResult:  # noqa: B008
    filename = file.filename or "resume"

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

    # Off the event loop (async-first rule, 2026-07-22 audit): a PDF/DOCX/ODT
    # parse of a multi-MiB resume is seconds of CPU — on the loop that starves
    # /healthz and gets the sidecar kill-restarted mid-onboarding.
    try:
        text = await asyncio.to_thread(extract_markdown, filename, data)
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ProfileIngestResult(text=text, filename=filename, chars=len(text))
