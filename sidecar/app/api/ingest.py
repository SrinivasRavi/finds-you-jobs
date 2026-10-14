"""Resume ingestion for onboarding (FR-OB-04 / US-OB-02).

POST /api/profile/ingest takes a file and returns its extracted text for review;
formats live in `app/doc_extract.py`. It never writes the DB: the reviewed text
is saved by POST /api/profile. An empty or undecodable extraction is a 422
asking the user to paste instead, never a garbled draft."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..doc_extract import ExtractionError, extract_markdown
from ..resume_check import resume_warnings

router = APIRouter()

# Upload guard: resumes are small documents. Cap the read so a stray large/binary
# upload can't balloon memory (the extractor runs fully in-process).
_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB


class ProfileIngestResult(BaseModel):
    """Extracted resume text held in the wizard draft for review (not persisted)."""

    text: str
    filename: str
    chars: int
    # `not_a_resume` / `instructions` (resume_check.py, S-C57); shown, never blocking.
    warnings: list[str] = []


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

    return ProfileIngestResult(
        text=text, filename=filename, chars=len(text), warnings=resume_warnings(text)
    )
