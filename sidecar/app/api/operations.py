"""The operations ledger: enqueue, read, list, retry, cancel, spans and the
all-time cost totals. Pydantic DTOs (dto.py) are the only wire types; ORM
never crosses this line.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Request

from ..registry import networker_ops as networker_ops
from ..registry.apply_op import (
    finalize_run_interrupted,
)
from . import dto
from .deps import db as _db
from .deps import found as _found
from .deps import runner as _runner

router = APIRouter()

# -- operations ------------------------------------------------------------


@router.post("/api/operations/{kind}", status_code=202)
def create_operation(
    request: Request,
    kind: str,
    input_snapshot: Annotated[dict[str, Any] | None, Body()] = None,
) -> dto.OperationAccepted:
    """Enqueue an operation; return its id immediately (architecture section 4.2)."""
    runner = _runner(request)
    # A few kinds are interactive/side-effectful and must go through their own
    # dedicated route (which does the P1-consent + resource setup), never the
    # generic enqueue: `linkedin_login` opens a headed browser (use
    # `/api/linkedin/connect`). The applier's `apply`/`prep` join this list when
    # they land.
    if kind == "linkedin_login":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/linkedin/connect to start a LinkedIn login",
        )
    if kind == "apply":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/applications/{id}/apply to start an apply run",
        )
    if kind == "linkedin_search":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/linkedin/search to run a logged-in LinkedIn job search",
        )
    if kind == "contact_sync":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/networking/contact-sync to refresh contact statuses",
        )
    if kind == "view_page":
        raise HTTPException(
            status_code=422,
            detail="use POST /api/browser/view to show a page on the browser surface",
        )
    if kind not in runner.known_kinds():
        raise HTTPException(status_code=404, detail=f"unknown operation kind {kind!r}")
    operation_id = runner.submit(kind, input_snapshot or {})
    return dto.OperationAccepted(id=operation_id, kind=kind, state="queued")


@router.post("/api/operations/{operation_id}/retry", status_code=202)
def retry_operation(request: Request, operation_id: str) -> dto.OperationAccepted:
    """Re-enqueue a failed operation with its original input snapshot — the Logs
    "App restarted while generating — retry?" affordance (US-LOG-01). Same kind,
    same inputs; a fresh operation id (the failed row stays as the audit record).
    `apply`/`linkedin_login` are excluded (interactive, non-generic paths)."""
    db = _db(request)
    with db.repos() as repos:
        op = _found(repos.operations.get(operation_id), "operation", operation_id)
        kind, snapshot = op.kind, dict(op.input_snapshot or {})
    if kind in ("apply", "linkedin_login"):
        raise HTTPException(
            status_code=422,
            detail=f"{kind} cannot be retried from the ledger — re-run it from its own surface",
        )
    new_id = _runner(request).submit(kind, snapshot)
    # Durable old→new link (no schema change: result_ref is JSON): a FAILED row
    # that was retried renders as "Retried" instead of nagging red forever,
    # while staying in the ledger as the honest cost/audit record (US-LOG-01).
    with db.repos() as repos:
        op = repos.operations.get(operation_id)
        if op is not None and op.state == "failed":
            op.result_ref = {**(op.result_ref or {}), "retried_as": new_id}
    return dto.OperationAccepted(id=new_id, kind=kind, state="queued")


@router.post("/api/operations/{operation_id}/cancel", status_code=202)
def cancel_operation(request: Request, operation_id: str) -> dto.OperationAccepted:
    """Cancel an operation (F-M7): a still-`queued` op (any kind) is cancelled
    outright; a `running` one is accepted ONLY for the kinds that cooperatively
    poll the cancel token (score/tailor/cover — `CANCELLABLE_RUNNING_KINDS`),
    landing `cancelled` at the entrypoint's next checkpoint. 404 for an unknown
    id; 409 when there is nothing this endpoint can honestly cancel — the op is
    already terminal, or it is running a kind that never observes the token
    (a running `apply` is cancelled via POST /api/apply-runs/{id}/cancel)."""
    runner = _runner(request)
    db = _db(request)
    with db.repos() as repos:
        op = _found(repos.operations.get(operation_id), "operation", operation_id)
        kind = op.kind
        # A queued `apply` op carries a durable ApplyRun row (start_apply creates
        # it before submit). If we cancel the op here, that row must be finalized
        # too — resolve its id now (snapshot `run_id`, else the operation link).
        apply_run_id = ""
        if kind == "apply":
            apply_run_id = str((op.input_snapshot or {}).get("run_id") or "")
            if not apply_run_id:
                existing = repos.apply_runs.get_by_operation(operation_id)
                apply_run_id = existing.id if existing is not None else ""
    if not runner.cancel(operation_id):
        raise HTTPException(
            status_code=409, detail=f"operation {operation_id!r} is not cancellable"
        )
    # Cancelling a QUEUED `apply` op strands its ApplyRun row live forever
    # (the entrypoint that would finalize it never runs), so start_apply's
    # single-flight keeps returning the dead run on every later Apply click.
    # Land it `interrupted` (never `failed`) — a user cancel. Guarded to ACTIVE
    # rows, so a run the entrypoint already finalized is left untouched. Only a
    # queued apply reaches here: a running apply is refused above (409) and
    # cancelled via POST /api/apply-runs/{id}/cancel instead (2026-07-25).
    if kind == "apply" and apply_run_id:
        finalize_run_interrupted(db, apply_run_id, "cancelled before the run started")
    # Honest post-cancel state: a queued op is already `cancelled`; a running
    # one is still `running` until its next cooperative checkpoint.
    with db.repos() as repos:
        op = repos.operations.get(operation_id)
        state = op.state if op is not None else "cancelled"
    return dto.OperationAccepted(id=operation_id, kind=kind, state=state)


# The ledger's per-row human subjects (US-LOG-01 legibility, maintainer
# directive 2026-08-03): WHICH entity each operation acted on, resolved from
# the row's own snapshot/result refs with one IN query per table per page
# (the `_application_dtos` batch-loader pattern — never per-row queries). A row
# whose refs don't resolve (historical snapshots, deleted entities) gets no
# subject and renders as before — nothing is fabricated. Every field is
# verbatim entity data; the frontend adds count nouns / mode names via i18n.

_JOB_SUBJECT_KINDS = frozenset({"score", "tailor", "cover"})
_CONTACT_SUBJECT_KINDS = frozenset({"draft", "send", "view_page"})


def _job_subject(job: Any | None) -> dto.OperationSubjectDTO | None:
    if job is None:
        return None
    return dto.OperationSubjectDTO(
        label=job.title, context=job.company or None, href=job.canonical_url or None
    )


def _first_line(text: str, limit: int = 60) -> str:
    """The first meaningful line of a pasted job description, for a ledger row
    that has no job row to name. Truncated because a ledger row is one line."""
    for line in text.splitlines():
        stripped = line.strip().lstrip("# ").strip()
        if stripped:
            return stripped[:limit]
    return ""


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


_ARTIFACT_SLOT = {"tailor": "tailored", "cover": "cover"}


def _operation_subject(
    op: Any,
    *,
    jobs: dict[str, Any],
    contacts: dict[str, Any],
    applications: dict[str, Any],
    artifacts: dict[str, Any],
) -> dto.OperationSubjectDTO | None:
    snap = op.input_snapshot or {}
    ref = op.result_ref or {}
    kind = op.kind

    if kind in _JOB_SUBJECT_KINDS:
        subject = _job_subject(jobs.get(str(snap.get("job_id") or "")))
        if subject is None:
            # No job behind it. That is a triage score, or a prompt-tuning test
            # run, and until this branch existed both rendered as a bare kind
            # with nothing to say (url-less-jobs.md drawing 13). A row the user
            # paid for has to say what it was.
            if not snap.get("prompt_test") and not ref:
                return None
            subject = dto.OperationSubjectDTO(
                label=_first_line(str(snap.get("job") or "")),
            )
        if snap.get("prompt_test"):
            subject.badge = "prompt test"
        elif kind == "score" and _int_or_none(ref.get("score")) is not None:
            method = str(ref.get("method") or "")
            subject.badge = f"{ref['score']} · {method}" if method else str(ref["score"])
        # A tailor or cover row wrote a document. Name where it lives so the
        # ledger can open it, rather than only linking out to the posting.
        slot = _ARTIFACT_SLOT.get(kind)
        artifact = artifacts.get(str(ref.get("artifact_id") or "")) if slot else None
        if artifact is not None:
            subject.artifact = dto.OperationArtifactDTO(
                application_id=artifact.application_id, kind=slot or ""
            )
        return subject
    if kind == "apply":
        app = applications.get(str(snap.get("application_id") or ""))
        return _job_subject(jobs.get(app.job_id) if app is not None else None)
    if kind in _CONTACT_SUBJECT_KINDS:
        contact = contacts.get(str(snap.get("contact_id") or ""))
        if contact is None:
            # A contact-less page view still names WHAT it showed: the URL's
            # path (compact), or the whole URL for a bare origin — never the
            # blank row that renders as the vague kind label (2026-08-16).
            if kind == "view_page":
                url = str(snap.get("url") or "")
                if not url:
                    return None
                path = urlparse(url).path
                label = path if path not in ("", "/") else url
                return dto.OperationSubjectDTO(label=label, href=url)
            return None
        job = jobs.get(str(snap.get("job_id") or ""))
        context = None
        if job is not None:
            context = f"{job.title} · {job.company}" if job.company else job.title
        # `send` carries the outgoing text in its snapshot; `draft` returns the
        # drafted preview in result_ref — either goes to the EXPANDED row only.
        detail = str(snap.get("message") or "") if kind == "send" else str(ref.get("message") or "")
        slug = networker_ops._public_id_from_url(contact.linkedin_url)
        return dto.OperationSubjectDTO(
            label=contact.name or slug,
            href=f"https://www.linkedin.com/in/{slug}" if slug else None,
            context=context,
            detail=detail or None,
        )
    if kind == "discover":
        label = str(
            ref.get("company_name") or snap.get("company_name") or snap.get("company") or ""
        )
        count = _int_or_none(ref.get("count"))
        if not label and count is None:
            return None
        return dto.OperationSubjectDTO(label=label, count=count)
    if kind == "linkedin_search":
        queries = ref.get("queries")
        label = ", ".join(str(q) for q in queries) if isinstance(queries, list) else ""
        mode = str((ref.get("search_cursor") or {}).get("mode") or snap.get("mode") or "")
        count = _int_or_none((ref.get("scan") or {}).get("persisted"))
        if not label and not mode and count is None:
            return None
        return dto.OperationSubjectDTO(label=label, context=mode or None, count=count)
    if kind == "scan":
        count = _int_or_none((ref.get("scan") or {}).get("persisted"))
        if count is None:
            return None
        sources = ref.get("per_source") or {}
        return dto.OperationSubjectDTO(context=", ".join(sorted(sources)) or None, count=count)
    if kind == "contact_sync":
        count = _int_or_none(ref.get("synced"))
        if count is None:
            return None
        transitions = ref.get("transitions") or {}
        detail = ", ".join(
            f"{key.replace('->', ' → ')} ×{value}" for key, value in sorted(transitions.items())
        )
        return dto.OperationSubjectDTO(count=count, detail=detail or None)
    if kind == "archive_stale_contacts":
        count = _int_or_none(ref.get("archived_count"))
        return dto.OperationSubjectDTO(count=count) if count is not None else None
    if kind == "cleanup_trash":
        if not ref:
            return None
        count = sum(
            _int_or_none(ref.get(key)) or 0
            for key in (
                "tombstoned_count",
                "expired_count",
                "expired_deleted_count",
                "purged_applications_count",
            )
        )
        return dto.OperationSubjectDTO(count=count)
    if kind == "linkedin_login":
        connected_as = str(ref.get("connected_as") or "")
        return dto.OperationSubjectDTO(label=connected_as) if connected_as else None
    if kind == "watch_company":
        label = str(snap.get("company") or snap.get("url") or "")
        if not label:
            return None
        return dto.OperationSubjectDTO(label=label, href=str(snap.get("url") or "") or None)
    return None


def _ledger_subjects(repos: Any, ops: list[Any]) -> dict[str, dto.OperationSubjectDTO]:
    """Batched subject pass over a page of ledger rows: gather every ref first,
    then ONE IN query per table (applications → jobs, contacts, artifacts)."""
    job_ids: set[str] = set()
    contact_ids: set[str] = set()
    application_ids: set[str] = set()
    artifact_ids: set[str] = set()
    for op in ops:
        snap = op.input_snapshot or {}
        if (op.kind in _JOB_SUBJECT_KINDS or op.kind in _CONTACT_SUBJECT_KINDS) and snap.get(
            "job_id"
        ):
            job_ids.add(str(snap["job_id"]))
        if op.kind in _CONTACT_SUBJECT_KINDS and snap.get("contact_id"):
            contact_ids.add(str(snap["contact_id"]))
        if op.kind == "apply" and snap.get("application_id"):
            application_ids.add(str(snap["application_id"]))
        if op.kind in _ARTIFACT_SLOT and (op.result_ref or {}).get("artifact_id"):
            artifact_ids.add(str(op.result_ref["artifact_id"]))
    applications = repos.applications.get_many(sorted(application_ids))
    job_ids.update(app.job_id for app in applications.values())
    jobs = repos.jobs.get_many(sorted(job_ids))
    contacts = repos.contacts.get_many(sorted(contact_ids))
    artifacts = repos.artifacts.get_many(sorted(artifact_ids))
    subjects: dict[str, dto.OperationSubjectDTO] = {}
    for op in ops:
        subject = _operation_subject(
            op, jobs=jobs, contacts=contacts, applications=applications, artifacts=artifacts
        )
        if subject is not None:
            subjects[op.id] = subject
    return subjects


@router.get("/api/operations")
def list_operations(request: Request, limit: int = 100) -> list[dto.OperationDTO]:
    """Recent operations — the ledger the Logs/Analytics surfaces read (section 10),
    each row carrying its batched-resolved human subject (US-LOG-01)."""
    with _db(request).repos() as repos:
        ops = repos.operations.list_recent(limit)
        subjects = _ledger_subjects(repos, ops)
        rows = [dto.operation_dto(op, subjects.get(op.id)) for op in ops]
    # A live send op's routed channel + completed steps (in-memory registry) —
    # lets a queue panel mounting mid-send render the fine steps (2026-08-16).
    for row in rows:
        if row.kind == "send" and row.state in ("queued", "running"):
            row.progress = networker_ops.send_progress(row.id)
    return rows


@router.get("/api/cost/totals")
def cost_totals(request: Request) -> dto.CostTotalsDTO:
    """All-time cost totals for the Analytics cost tiles (FR-SET-07 / US-LOG-01 #2).

    Live-ledger sum + the persisted pruned-ops aggregate (NFR-COST-02: the
    running spend total stays honest as an install ages). Since 2026-08-24
    nothing prunes, so the aggregate sits at zero and the live sum carries every
    operation ever recorded; the addition stays because a future S-C22 deletion
    policy has to be able to fold spend forward again.

    Off the loop: summing every operation is inherently linear, so however cheap
    the read gets it must not be the thing holding a 2 s health poll (S-C23) —
    a plain `def` handler runs in Starlette's threadpool automatically."""
    with _db(request).repos() as repos:
        return dto.cost_totals_dto(repos.all_time_cost_totals())


@router.get("/api/operations/{operation_id}")
def get_operation(request: Request, operation_id: str) -> dto.OperationDTO:
    with _db(request).repos() as repos:
        op = _found(repos.operations.get(operation_id), "operation", operation_id)
        return dto.operation_dto(op, _ledger_subjects(repos, [op]).get(op.id))


@router.get("/api/operations/{operation_id}/spans")
def get_operation_spans(request: Request, operation_id: str) -> list[dto.SpanDTO]:
    """The Logfire spans for one operation — the Logs drill-down (US-SYS-05 / A6).

    Reads the local `logfire.sqlite` span store (never the app schema). Returns
    an empty list when observability isn't configured or the op has no spans yet
    — the row still shows its ledger + verbatim error, so this only *enriches*."""
    from ..observability import read_spans_for_operation

    obs = getattr(request.app.state, "observability", None)
    if obs is None or getattr(obs, "span_db_path", None) is None:
        return []
    rows = read_spans_for_operation(obs.span_db_path, operation_id)
    return [dto.SpanDTO(**row) for row in rows]
