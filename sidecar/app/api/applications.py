"""Tracker routes: applications, their documents, packets, artifacts and
activity, and the Applier's apply runs
(docs/internal/archived/applier-as-built.md section 8).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from sidecar.modules.scraper.fingerprint import (
    simhash64,
    to_signed64,
)
from sidecar.modules.scraper.quality import evaluate as quality_evaluate

from .. import documents as docstore
from ..db.base import now_utc
from ..db.models import APPLY_RUN_ACTIVE_STATUSES, OP_ACTIVE_STATES, OP_ALL_STATES
from ..priority import STATS_KEY, zband_priority
from ..registry import networker_ops as networker_ops
from ..registry.apply_op import (
    NO_POSTING_URL_DETAIL,
    advance_card_to_applied,
    finalize_run_failed,
    finalize_run_interrupted,
    purge_run_dirs,
)
from ..registry.persistence import (
    delete_application_cascade,
)
from . import dto
from .deps import db as _db
from .deps import found as _found
from .deps import runner as _runner
from .jobs import (
    _NO_IDENTITY_422_DETAIL,
    _TOMBSTONE_409_DETAIL,
    _current_profile_version,
    _display_score_value,
    _identity,
)
from .packet import (
    PACKET_KINDS,
    auto_cover_default,
    auto_resume_default,
    enqueue_packet,
)

router = APIRouter()

# -- applications (with derived packetState) --------------------------------


def _application_dtos(repos: Any, applications: list[Any]) -> list[dto.ApplicationDTO]:
    """Assemble ApplicationDTOs for a batch of cards (F-H2): the card-invariant
    lookups (profile version, score-op scan, send/discover op lists) run ONCE
    per call, and every child table is fetched with one IN(ids) query — so the
    tracker list at N cards issues a constant number of queries, never ~12+ per
    card."""
    if not applications:
        return []
    app_ids = [a.id for a in applications]
    job_ids = list({a.job_id for a in applications})
    # Card-invariant lookups — hoisted out of the per-card loop.
    score_op_states = repos.operations.score_states_by_job()
    send_ops = repos.operations.list_by_kind_states(
        "send", {"queued", "running", "failed", "succeeded"}
    )
    discover_ops = repos.operations.list_by_kind_states(
        "discover", OP_ACTIVE_STATES
    )
    # Batched child rows — one IN query per table.
    # Only head artifacts (not superseded) surface + drive packetState.
    artifacts_by_app: dict[str, list[Any]] = {}
    for artifact in repos.artifacts.list_for_applications(app_ids):
        if artifact.superseded_by is None:
            artifacts_by_app.setdefault(artifact.application_id, []).append(artifact)
    ops_by_id = repos.operations.get_many(
        [
            a.operation_id
            for artifacts in artifacts_by_app.values()
            for a in artifacts
            if a.operation_id is not None
        ]
    )
    jobs_by_id = repos.jobs.get_many(job_ids)
    # The tracker shows the same number the board does, so it reads it through
    # the same lens. One extra indexed read per page, and none at all until the
    # user sets a weight.
    lens = dto.build_weight_lens(repos, list(job_ids))
    sent_counts = repos.outreach_logs.count_sent_for_jobs(job_ids)
    latest_batches = repos.outreach_logs.latest_batches_for_jobs(job_ids)
    jobs_with_candidates = repos.referral_candidates.job_ids_with_contacts(job_ids)
    docs_by_app: dict[str, list[Any]] = {}
    for doc in repos.documents.list_for_applications(app_ids):
        docs_by_app.setdefault(doc.application_id, []).append(doc)
    latest_runs = repos.apply_runs.latest_for_applications(app_ids)

    results: list[dto.ApplicationDTO] = []
    for application in applications:
        job_id = application.job_id
        with_states: list[tuple[Any, str | None]] = []
        for artifact in artifacts_by_app.get(application.id, []):
            state: str | None = None
            if artifact.operation_id is not None:
                op = ops_by_id.get(artifact.operation_id)
                state = op.state if op is not None else None
            with_states.append((artifact, state))
        job = jobs_by_id.get(job_id)
        job_dto_val = None
        if job is not None:
            job_dto_val = dto.job_dto(
                job, score_op_states=score_op_states.get(job_id), lens=lens
            )
        # Referral progress (FR-NW-01 canonical enum): landed-send count, in-flight
        # send ops, whether a discover op is running, whether a roster was found for
        # the role, and the latest reach-out batch's outcomes.
        send_states = [
            op.state for op in send_ops if op.job_id == job_id
        ]
        discover_in_flight = any(
            op.job_id == job_id for op in discover_ops
        )
        # Attached documents (manual cards) — the resume/cover the user submitted.
        attached_docs = docs_by_app.get(application.id, [])
        results.append(
            dto.application_dto(
                application,
                with_states,
                job=job_dto_val,
                referrals_count=sent_counts.get(job_id, 0),
                referrals_op_states=send_states,
                discover_in_flight=discover_in_flight,
                has_candidates=job_id in jobs_with_candidates,
                latest_batch_outcomes=[
                    log.outcome for log in latest_batches.get(job_id, [])
                ],
                latest_apply_run=latest_runs.get(application.id),
                documents=attached_docs,
            )
        )
    return results


def _application_dto(repos: Any, application: Any) -> dto.ApplicationDTO:
    return _application_dtos(repos, [application])[0]


@router.get("/api/applications")
def list_applications(
    request: Request, include_archived: bool = False
) -> list[dto.ApplicationDTO]:
    # Assembly runs off the event loop (async-first rule / F-H2): a plain `def`
    # handler runs in Starlette's threadpool automatically.
    with _db(request).repos() as repos:
        apps = repos.applications.list(include_archived=include_archived)
        return _application_dtos(repos, apps)


@router.post("/api/applications", status_code=201)
def create_application(
    request: Request, payload: dto.ApplicationCreate
) -> dto.ApplicationDTO:
    db = _db(request)
    # 1. Create + commit the Application first (the worker must see it).
    with db.repos() as repos:
        _found(repos.jobs.get(payload.job_id), "job", payload.job_id)
        # One card per role (`applications.job_id` is unique). Answered here so a
        # double-click on Save reads as a clean conflict rather than surfacing
        # the IntegrityError as a 500.
        if payload.job_id in repos.applications.job_ids():
            raise HTTPException(
                status_code=409, detail="this job is already on the tracker"
            )
        prefs = repos.preferences.get_or_create()
        # Priority assignment (FR-TR-09): an explicit value is a manual choice;
        # otherwise the z-band of the job's current score, or P0 if saved while
        # the score is still Pending (the strongest signal — skips the z-band).
        if payload.priority is not None:
            priority = payload.priority
        else:
            job = repos.jobs.get(payload.job_id)
            value = _display_score_value(job) if job is not None else -1
            if value < 0:
                priority = "P0"
            else:
                priority = zband_priority((prefs.thresholds or {}).get(STATS_KEY), value)
        app = repos.applications.create(
            payload.job_id,
            column=payload.column,
            priority=priority,
            notes_markdown=payload.notes_markdown,
        )
        application_id = app.id
        auto_resume = auto_resume_default(prefs.thresholds)
        auto_cover = auto_cover_default(prefs.thresholds)

    # 2. Per-job automation toggles (US-TL-03): split defaults (FR-SET-02).
    # (The prior repository also enqueued Save-time form prep here; retired.)
    resume = payload.generate_resume if payload.generate_resume is not None else auto_resume
    cover = payload.generate_cover if payload.generate_cover is not None else auto_cover
    if resume or cover:
        enqueue_packet(
            db,
            _runner(request),
            application_id=application_id,
            job_id=payload.job_id,
            resume=resume,
            cover=cover,
            guidance=payload.guidance,
        )

    with db.repos() as repos:
        return _application_dto(repos, repos.applications.get(application_id))


# Manual application `column` values shown on the Tracker card menu — the
# post-referral pipeline stages a real-world "already applied" record can land in.
_MANUAL_COLUMNS = {"applied", "interviewing", "offer", "rejected"}


@router.post("/api/applications/manual", status_code=201)
async def create_manual_application(
    request: Request,
    # Recommended, not required (url-less-jobs.md section 5.1): a card logged
    # from an email may have no link, and the description keys it instead.
    canonical_url: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    company: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    posted_at: Annotated[str, Form()] = "",
    salary: Annotated[str, Form()] = "",
    source_adapter: Annotated[str, Form()] = "paste-url",
    column: Annotated[str, Form()] = "applied",
    notes_markdown: Annotated[str, Form()] = "",
    resume: Annotated[UploadFile | None, File()] = None,
    cover: Annotated[UploadFile | None, File()] = None,
) -> dto.ApplicationDTO:
    """Log a job the user already applied to OUTSIDE the app ("Add a job
    application" — the Tracker sibling of the board's Add a job posting). Upserts the
    job with the same dedup/tombstone discipline, creates the card as
    `origin=manual` in a post-referral stage (default Applied), and attaches the
    resume/cover the user actually submitted (optional) as content-addressed,
    deduped documents. No score is enqueued — they already applied, so fit
    ranking is moot."""
    db = _db(request)
    canonical, digest = _identity(canonical_url, description)
    if canonical is None and digest is None:
        raise HTTPException(status_code=422, detail=_NO_IDENTITY_422_DETAIL)
    stage = column or "applied"
    if stage not in _MANUAL_COLUMNS:
        raise HTTPException(
            status_code=422,
            detail=f"stage must be one of {sorted(_MANUAL_COLUMNS)}",
        )

    # 1. Read + validate any uploads up front (fail fast before we create rows).
    prepared: list[tuple[str, bytes, str]] = []  # (kind, data, filename)
    for kind, upload in (("tailored_resume", resume), ("cover_letter", cover)):
        if upload is None or not upload.filename:
            continue
        data = await upload.read()
        try:
            docstore.validate(upload.filename, data)
        except (docstore.DocumentTooLarge, docstore.UnsupportedDocumentType) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        prepared.append((kind, data, upload.filename))

    # 2. Upsert the job + create the manual card (refusing to double-track).
    with db.repos() as repos:
        if repos.tombstones.exists(canonical or ""):
            raise HTTPException(status_code=409, detail=_TOMBSTONE_409_DETAIL)
        existing_job = repos.jobs.get_by_identity(
            canonical_url=canonical or "", content_hash=digest or ""
        )
        if existing_job is not None:
            job = existing_job
            if job.feed_state == "removed":
                job = repos.jobs.set_trash_state(job.id, trashed=False)
            if job.id in repos.applications.job_ids():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This job is already in your tracker — move that card to "
                        "Applied instead of adding it again."
                    ),
                )
        else:
            # The same checks the board's form runs: the scam check, so a
            # red-flag job is flagged and capped here too, and the duplicate
            # check, so a later board copy says it's already in the tracker.
            trust_score, trust_flags = quality_evaluate(
                title=title or canonical or "",
                location=location,
                description=description,
                canonical_url=canonical or "",
                posted_at=posted_at or None,
            )
            job = repos.jobs.create(
                canonical_url=canonical,
                content_hash=digest,
                fingerprint=to_signed64(simhash64(description)) or None,
                title=title or canonical or "",
                company=company,
                location=location,
                description=description,
                posted_at=posted_at or None,
                salary=salary or None,
                source_adapter=source_adapter or "paste-url",
                origin="manual",
                trust_score=trust_score,
                trust_flags=trust_flags,
            )
            from ..registry.persistence import flag_near_duplicate

            flag_near_duplicate(repos, job)
        app = repos.applications.create(
            job.id,
            column=stage,
            origin="manual",
            applied_via="manual",
            notes_markdown=notes_markdown,
        )
        application_id = app.id

    # 3. Store uploads off the event loop (deduped on disk), then index + link
    #    them — only now that the card exists (no orphan blobs on the 409 path).
    stored: list[tuple[str, str, int, str, str]] = []  # kind, sha, size, mime, name
    for kind, data, filename in prepared:
        sha = await asyncio.to_thread(docstore.store_bytes, data, db.data_dir)
        stored.append(
            (kind, sha, len(data), docstore.mime_for_filename(filename), filename)
        )
    if stored:
        with db.repos() as repos:
            for kind, sha, size, mime, filename in stored:
                repos.documents.set(
                    application_id,
                    kind,
                    sha256=sha,
                    byte_size=size,
                    mime_type=mime,
                    original_filename=filename,
                )

    with db.repos() as repos:
        return _application_dto(repos, repos.applications.get(application_id))


@router.post("/api/applications/{application_id}/documents", status_code=201)
async def attach_application_document(
    request: Request,
    application_id: str,
    kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> dto.ApplicationDTO:
    """Attach a resume/cover FILE to an EXISTING application — the Upload button
    on the Tailored resume / Cover letter editors. Stores it content-addressed
    (deduped) and links it as the application's `kind` slot, replacing any prior
    file for that kind (one resume + one cover per card). This is the exact
    document the user submits on Apply / the record for a manual card logged
    without a file. `kind` ∈ {'tailored_resume', 'cover_letter'}."""
    if kind not in PACKET_KINDS:
        raise HTTPException(
            status_code=422, detail=f"kind must be one of {list(PACKET_KINDS)}"
        )
    db = _db(request)
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=422, detail="No file was uploaded.")
    data = await file.read()
    try:
        docstore.validate(filename, data)
    except (docstore.DocumentTooLarge, docstore.UnsupportedDocumentType) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Fail before storing the blob if the card is gone (no orphan on disk).
    with db.repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)

    sha = await asyncio.to_thread(docstore.store_bytes, data, db.data_dir)
    with db.repos() as repos:
        _, orphaned = repos.documents.set(
            application_id,
            kind,
            sha256=sha,
            byte_size=len(data),
            mime_type=docstore.mime_for_filename(filename),
            original_filename=filename,
        )
        result = _application_dto(repos, repos.applications.get(application_id))
    # Replacing a file leaves its blob unreferenced. Unlink after the commit, so
    # a rolled-back write never deletes bytes a surviving row still names.
    if orphaned is not None:
        await asyncio.to_thread(docstore.delete_blob, orphaned, db.data_dir)
    return result


@router.delete("/api/applications/{application_id}/documents/{kind}")
def detach_application_document(
    request: Request, application_id: str, kind: str
) -> dto.ApplicationDTO:
    """Detach the (application, kind) resume/cover file — the ✕ on the attached-
    file chip. The blob is unlinked too, once no row names it: before the table
    merge nothing ever collected it and every detach leaked a file (S-C37)."""
    if kind not in PACKET_KINDS:
        raise HTTPException(
            status_code=422, detail=f"kind must be one of {list(PACKET_KINDS)}"
        )
    db = _db(request)
    with db.repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)
        orphaned = repos.documents.delete(application_id, kind)
        result = _application_dto(repos, repos.applications.get(application_id))
    if orphaned is not None:
        docstore.delete_blob(orphaned, db.data_dir)
    return result


@router.get("/api/documents/{document_id}")
def download_document(request: Request, document_id: str) -> FileResponse:
    """Serve an uploaded document verbatim (the resume/cover a user attached to a
    manual card). Content-addressed on disk; streamed with its original name."""
    db = _db(request)
    with db.repos() as repos:
        doc = _found(repos.documents.get(document_id), "document", document_id)
        sha, mime, filename = doc.sha256, doc.mime_type, doc.original_filename
    path = docstore.blob_path(sha, db.data_dir)
    if not path.exists():
        raise HTTPException(status_code=404, detail="document blob is missing on disk")
    return FileResponse(
        path,
        media_type=mime or "application/octet-stream",
        filename=filename or sha,
    )


@router.post("/api/applications/{application_id}/packet", status_code=202)
def generate_packet(
    request: Request, application_id: str, payload: dto.PacketRequest
) -> dto.ApplicationDTO:
    """Manual/regenerate packet build (US-TL-02) — supersedes prior artifacts."""
    db = _db(request)
    with db.repos() as repos:
        app = _found(repos.applications.get(application_id), "application", application_id)
        job_id = app.job_id
    enqueue_packet(
        db,
        _runner(request),
        application_id=application_id,
        job_id=job_id,
        resume=payload.resume,
        cover=payload.cover,
        guidance=payload.guidance,
    )
    with db.repos() as repos:
        return _application_dto(repos, repos.applications.get(application_id))


@router.patch("/api/applications/{application_id}/artifacts/{kind}")
def patch_artifact(
    request: Request, application_id: str, kind: str, payload: dto.ArtifactPatch
) -> dto.ApplicationDTO:
    """Persist an edited variant + the Approve-and-Save flip (US-RES-02 / FR-RES-02).

    Targets the head (non-superseded) artifact of `kind` for this application.
    `markdown` overwrites the text (edits apply only to this variant — the master
    is untouched); `approved` stamps/clears `approved_at` (the `ready ⇄ approved`
    flip). Per-artifact, so the resume and cover letter are approved separately."""
    if kind not in PACKET_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown artifact kind {kind!r}")
    with _db(request).repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)
        head = next(
            (
                a
                for a in repos.artifacts.list_for_application(application_id)
                if a.kind == kind and a.superseded_by is None
            ),
            None,
        )
        if head is None:
            # No generated variant exists — the user pasted or typed their own
            # (for example, from their own ChatGPT or Gemini subscription). Create
            # a manual artifact with no operation, so packetState derives to
            # `ready` (editable and approvable). An approve-only flip with no text
            # has nothing to create, so it still 404s.
            if payload.markdown is None:
                raise HTTPException(status_code=404, detail=f"no {kind} artifact to update")
            repos.artifacts.create(
                application_id,
                kind=kind,
                markdown=payload.markdown,
                notes=[],
                profile_version=_current_profile_version(repos),
                guidance_used=None,
                operation_id=None,
                approved_at=now_utc() if payload.approved else None,
            )
            repos.applications.update(application_id, last_touched_at=now_utc())
            return _application_dto(repos, repos.applications.get(application_id))
        fields: dict[str, Any] = {}
        if payload.markdown is not None:
            fields["markdown"] = payload.markdown
        if payload.approved is not None:
            fields["approved_at"] = now_utc() if payload.approved else None
        if fields:
            repos.artifacts.update(head.id, **fields)
            # Persisting a review is a touch on the card (last-touched clock).
            repos.applications.update(application_id, last_touched_at=now_utc())
        return _application_dto(repos, repos.applications.get(application_id))


@router.get("/api/applications/{application_id}")
def get_application(request: Request, application_id: str) -> dto.ApplicationDTO:
    with _db(request).repos() as repos:
        app = _found(repos.applications.get(application_id), "application", application_id)
        return _application_dto(repos, app)


_SCORE_LABELS = {"failed": "Score failed", "succeeded": "Scored"}


def _ops_for_job(repos: Any, kind: str, job_id: str) -> list[Any]:
    return repos.operations.list_for_job(kind, OP_ALL_STATES, job_id)


def _column_label(column: str) -> str:
    """Humanize a pipeline column id for the Activity label (e.g.
    `seeking_referral` → `Seeking Referral`)."""
    return column.replace("_", " ").title()


def _event_label(kind: str, detail: dict[str, Any]) -> str:
    """The Activity-tab label for a user-driven card event (FR-TR-03/04)."""
    if kind == "column_change":
        frm = _column_label(str(detail.get("from", "")))
        to = _column_label(str(detail.get("to", "")))
        return f"Moved from {frm} to {to}"
    if kind == "notes":
        return "Notes updated"
    if kind == "archive":
        return "Archived"
    if kind == "unarchive":
        return "Restored from archive"
    return kind


@router.get("/api/applications/{application_id}/activity")
def application_activity(
    request: Request, application_id: str
) -> list[dto.ActivityEntryDTO]:
    """Real Activity log for one application (US-TR-03 / FR-TR-03) — composed from
    the operations ledger + card events, never synthesized client-side. Records:
    added-to-tracker, score, tailor/cover generation, column moves, notes edits,
    archive/unarchive. (Apply + outreach entries return with their commits.)"""
    with _db(request).repos() as repos:
        app = _found(repos.applications.get(application_id), "application", application_id)
        entries: list[dto.ActivityEntryDTO] = [
            dto.ActivityEntryDTO(kind="added", label="Added to tracker", at=app.saved_at)
        ]
        # Score ops for the job.
        for op in _ops_for_job(repos, "score", app.job_id):
            if op.state in ("succeeded", "failed"):
                entries.append(
                    dto.ActivityEntryDTO(
                        kind="score",
                        label=_SCORE_LABELS.get(op.state, "Scoring"),
                        state=op.state,
                        at=op.finished_at or op.created_at,
                    )
                )
        # Tailor / cover artifacts (head + superseded — the full generation trail).
        for artifact in repos.artifacts.list_for_application(application_id):
            op = (
                repos.operations.get(artifact.operation_id)
                if artifact.operation_id is not None
                else None
            )
            noun = "Tailored resume" if artifact.kind == "tailored_resume" else "Cover letter"
            state = op.state if op is not None else "succeeded"
            verb = "generation failed" if state == "failed" else "generated"
            entries.append(
                dto.ActivityEntryDTO(
                    kind="tailor" if artifact.kind == "tailored_resume" else "cover",
                    label=f"{noun} {verb}",
                    state=state,
                    at=(op.finished_at if op is not None else None) or artifact.created_at,
                )
            )
        # User-driven card events (FR-TR-03/04): column moves, notes edits, archive.
        for event in repos.application_events.list_for_application(application_id):
            entries.append(
                dto.ActivityEntryDTO(
                    kind=event.kind,
                    label=_event_label(event.kind, event.detail),
                    at=event.created_at,
                )
            )
    entries.sort(key=lambda e: (e.at is None, e.at))
    return entries


@router.patch("/api/applications/{application_id}")
def update_application(
    request: Request, application_id: str, payload: dto.ApplicationUpdate
) -> dto.ApplicationDTO:
    """Move/annotate/archive a card. Column moves, notes edits, and
    archive/unarchive each write an `ApplicationEvent` (only on real change —
    a no-op PATCH records nothing). `intent` is the section 5.1 exclusive value:
    setting it replaces the previous one wholesale."""
    fields = payload.model_dump(exclude_none=True)
    archived_flag = fields.pop("archived", None)
    with _db(request).repos() as repos:
        existing = _found(
            repos.applications.get(application_id), "application", application_id
        )
        events: list[tuple[str, dict[str, Any]]] = []
        if "column" in fields and fields["column"] != existing.column:
            events.append(("column_change", {"from": existing.column, "to": fields["column"]}))
        if "notes_markdown" in fields and fields["notes_markdown"] != existing.notes_markdown:
            events.append(("notes", {}))
        if archived_flag is not None:
            currently_archived = existing.archived_at is not None
            if archived_flag and not currently_archived:
                events.append(("archive", {}))
            elif not archived_flag and currently_archived:
                events.append(("unarchive", {}))
            fields["archived_at"] = now_utc() if archived_flag else None
        app = repos.applications.update(application_id, **fields)
        for kind, detail in events:
            repos.application_events.create(application_id, kind, detail)
        return _application_dto(repos, app)


@router.delete("/api/applications/{application_id}", status_code=204)
def delete_application(request: Request, application_id: str) -> None:
    """Remove a card (unsave / return-to-board — US-JB / US-TR-07)."""
    with _db(request).repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)
        _deleted, run_ids, orphaned = delete_application_cascade(repos, application_id)
    # F-M8: the runs' on-disk artifacts (frozen PDF + step PNGs) go with the
    # rows, and so do the uploaded blobs nothing names any more (S-C37).
    # Best-effort + path-guarded; off-loop via Starlette's threadpool (rmtree
    # and unlink block).
    if run_ids:
        purge_run_dirs(run_ids)
    for sha in orphaned:
        docstore.delete_blob(sha, _db(request).data_dir)


# ---------------------------------------------------------------------------
# Applier (docs/internal/archived/applier-as-built.md section 8) — direct apply runs off the Tracker
# ---------------------------------------------------------------------------


@router.post("/api/applications/{application_id}/apply", status_code=202)
def start_apply(
    request: Request,
    application_id: str,
    payload: dto.ApplyStartRequest | None = None,
) -> dto.ApplyRunDTO:
    """Create the durable ApplyRun and enqueue the `apply` op immediately —
    no pre-Apply confirmation modal (section 8.1); the click IS the action. Clicking
    Apply also settles the exclusive intent to `apply` (roadmap section 5.1)."""
    payload = payload or dto.ApplyStartRequest()
    runner = _runner(request)
    db = _db(request)
    # Read phase — NO writes in this session: a pending write would autoflush
    # on the next query and hold SQLite's write lock while runner.submit
    # inserts on its own connection (the 2026-07-17 "database is locked").
    with db.repos() as repos:
        app_row = _found(
            repos.applications.get(application_id), "application", application_id
        )
        job = _found(repos.jobs.get(app_row.job_id), "job", app_row.job_id)
        active = repos.apply_runs.latest_for_application(application_id)
        if active is not None and active.status in APPLY_RUN_ACTIVE_STATUSES:
            # Single-flight per card: reopening the companion binds to the
            # active run instead of double-launching a browser (section 8.2).
            return dto.apply_run_dto(active)
        # Before `apply_runs.create`: a run against a job with no link would sit in the
        # panel as a failure the user can't act on. 409, so the card opens Add link
        # (url-less-jobs.md sections 5.4 and 9.8). The dev seam carries its own form: a
        # local fixture is a file:// URL, which `canonicalize_url` rejects, so it can't
        # live on the job row. Production has no dev knobs; a URL-less card still 409s.
        dev_form_url = str((payload.dev or {}).get("form_url") or "")
        if not job.canonical_url and not dev_form_url:
            raise HTTPException(status_code=409, detail=NO_POSTING_URL_DETAIL)
        needs_intent = app_row.intent != "apply"
        job_url = job.canonical_url or dev_form_url

    snapshot: dict[str, Any] = {"application_id": application_id}
    if payload.retry_of_run_id:
        snapshot["retry_of_run_id"] = payload.retry_of_run_id
    if payload.dev:
        snapshot.update({f"dev_{k}": v for k, v in payload.dev.items()})

    # Write phase: settle the intent (section 5.1) and create the run in its own
    # committed transaction before enqueueing the op. The worker can dispatch within
    # ms and must adopt this row by `run_id`; creating it after submit raced the op,
    # which minted a duplicate run. QUEUED until the op starts.
    with db.repos() as repos:
        if needs_intent:
            repos.applications.update(application_id, intent="apply")
        run = repos.apply_runs.create(
            application_id,
            retry_of_run_id=payload.retry_of_run_id,
            source_url=job_url,
            status="queued",
            phase="queued",
        )
        run_id = run.id
    snapshot["run_id"] = run_id
    try:
        operation_id = runner.submit("apply", snapshot)
    except Exception as exc:
        # Enqueue failed (2026-07-25): the just-created row would strand
        # `queued` until boot sweep — land it terminal-failed, then let the
        # error surface exactly as it otherwise would (flight-recorded 500).
        finalize_run_failed(db, run_id, f"enqueue failed — {type(exc).__name__}: {exc}")
        raise

    with db.repos() as repos:
        # Attach the ledger link; the op writes the same value on adoption, so
        # whichever lands second is a no-op. Progress columns are untouched.
        run = repos.apply_runs.update(run_id, operation_id=operation_id)
        return dto.apply_run_dto(run)


@router.get("/api/applications/{application_id}/apply-runs")
def list_apply_runs(
    request: Request, application_id: str
) -> list[dto.ApplyRunDTO]:
    with _db(request).repos() as repos:
        _found(repos.applications.get(application_id), "application", application_id)
        return [
            dto.apply_run_dto(r)
            for r in repos.apply_runs.list_for_application(application_id)
        ]


@router.get("/api/apply-runs/{run_id}")
def get_apply_run(request: Request, run_id: str) -> dto.ApplyRunDTO:
    """The run snapshot — a reopened companion fetches this instead of
    depending on having seen every prior SSE event (section 9.2)."""
    with _db(request).repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
        return dto.apply_run_dto(run)


@router.get("/api/apply-runs/{run_id}/screenshots/{index}")
def get_apply_run_screenshot(
    request: Request, run_id: str, index: int
) -> FileResponse:
    """Serve one evidence PNG by index. Paths come from the run row only —
    never from the client — so this cannot read arbitrary files."""
    with _db(request).repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
        shots = list(run.screenshots)
    if not (0 <= index < len(shots)):
        raise HTTPException(status_code=404, detail="no such screenshot")
    path = Path(shots[index])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="screenshot file missing")
    return FileResponse(path, media_type="image/png")


@router.post("/api/apply-runs/{run_id}/cancel")
def cancel_apply_run(request: Request, run_id: str) -> dto.ApplyRunDTO:
    """Cooperative cancel (section 8.2). The loop notices between steps and lands the
    run as `interrupted`; an already-terminal run is returned unchanged."""
    from ..registry.apply_op import APPLY_CONTROL

    runner = _runner(request)
    db = _db(request)
    cancelled_queued = False
    with db.repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
        if run.operation_id and run.operation_id in APPLY_CONTROL:
            # In flight: cooperative — the loop notices between steps.
            APPLY_CONTROL[run.operation_id].cancel()
        elif run.operation_id and run.status == "queued":
            # Still queued: cancel the op outright and land the run honestly.
            cancelled_queued = runner.cancel(run.operation_id)
        if not cancelled_queued:
            return dto.apply_run_dto(run)
    # The landing goes through the ONE interrupted-transition implementation
    # (D-A7) — the same call the generic operations-cancel route makes, so the
    # two cancel surfaces can never write different terminal state.
    finalize_run_interrupted(db, run_id, "cancelled before the run started")
    with db.repos() as repos:
        return dto.apply_run_dto(_found(repos.apply_runs.get(run_id), "run", run_id))


@router.post("/api/apply-runs/{run_id}/submit", status_code=202)
def submit_apply_run(request: Request, run_id: str) -> dto.ApplyRunDTO:
    """Click the form's Submit once, because the USER asked (S-A5).

    P1 keeps every irreversible action behind the human: the model's tool
    vocabulary has no submit, and this route is the only thing that can reach
    `submit_application`. It needs the run's own browser still open, which is
    the review window after `ready_for_human`, so a run outside that window is
    a 409 rather than a silent no-op. The click itself happens on the op's
    thread; the outcome arrives on the `apply` SSE stream and on the run row.
    """
    from ..registry.apply_op import APPLY_CONTROL

    with _db(request).repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
        if run.status != "ready_for_human":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"run is {run.status!r}; Submit needs the review window that "
                    "follows ready_for_human"
                ),
            )
        control = APPLY_CONTROL.get(run.operation_id or "")
        if control is None:
            raise HTTPException(
                status_code=409,
                detail="the run's browser is no longer open; submit in it yourself and attest",
            )
        control.request_submit()
        return dto.apply_run_dto(run)


@router.post("/api/apply-runs/{run_id}/attest")
def attest_apply_run(
    request: Request, run_id: str, payload: dto.ApplyAttestRequest
) -> dto.ApplyRunDTO:
    """The human's word after the P1 handoff (section 8.4): 'I submitted' records a
    user-attested submission and advances the card to Applied; 'didn't submit'
    leaves the card in its pre-submission column with the honest run result."""
    with _db(request).repos() as repos:
        run = _found(repos.apply_runs.get(run_id), "run", run_id)
        if run.status not in ("ready_for_human", "interrupted", "timed_out", "blocked"):
            if not (run.status == "submitted" and payload.submitted):
                raise HTTPException(
                    status_code=409,
                    detail=f"run is {run.status!r}; attestation applies after the handoff",
                )
        if payload.submitted and run.status != "submitted":
            run = repos.apply_runs.update(
                run_id, status="submitted", submit_evidence="user_attested"
            )
            advance_card_to_applied(repos, run.application_id, by="user_attested")
        return dto.apply_run_dto(run)
