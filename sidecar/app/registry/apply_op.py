"""The Applier operation (applier-as-built.md section 8): 1 `apply` op from the
Tracker, driving `jobapplier.run_apply`.

The route creates the ApplyRun; this waits for the packet, freezes the
artifact, opens the run's tab in the shared Applier window
(applier/window.md) and renders the PDF. The loop has no submit tool (4.2). On
`ready_for_human` the tab stays open for review (8.4): a confirmation page
records `submitted`, else the user may attest. Tests inject `ENGINE_FACTORY`;
`FYJ_APPLY_DEV=1` honors the snapshot's dev knobs."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable, Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any

from sidecar.packages.jobapplier import (
    ApplyControl,
    ApplyEvent,
    ApplyRequest,
    ApplyResult,
    ApplyStatus,
    ArtifactRef,
    PageState,
    UrlPolicy,
    classify,
    observe,
    run_apply,
    submit_application,
)
from sidecar.packages.jobapplier.fake import FakeApplyEngine
from sidecar.packages.jobapplier.loop import ApplyEngine

from ...modules._shared.completion_retry import CompletionCancelled
from ..api.browser import is_browser_missing
from ..db import Repos
from ..db.base import now_utc
from ..db.database import Database, resolve_data_dir
from ..db.models import APPLY_RUN_ACTIVE_STATUSES, OP_ACTIVE_STATES
from ..events import make_event
from .applier_window import (
    BROWSER_MISSING_DETAIL,
    ApplierWindow,
    BrowserMissingError,
    window_for,
)
from .engines import EngineNotConfiguredError
from .operations import OperationContext, OperationOutcome
from .pdf import render_resume_pdf_async

# Cancel plumbing: the cancel route flips the control for a live operation.
APPLY_CONTROL: dict[str, ApplyControl] = {}

_PACKET_POLL_S = 2.0
_PACKET_WAIT_MAX_S = 15 * 60.0
# Why an Apply can't start, shown by the route's refusal and raised by the
# retry path's guard (url-less-jobs.md section 5.4).
NO_POSTING_URL_DETAIL = (
    "This job has no posting link, so there's no form to apply on. "
    "Add the link and try again."
)
# The Applier fills forms from the facts extracted from the master resume. Without
# them it would fill blind, so Apply refuses (audit A5).
NO_APPLICATION_PROFILE_DETAIL = (
    "Check your applicant details before your first application: your name, contact "
    "details and work authorization, in Settings > Applying > Applicant details. The "
    "Applier fills forms from them."
)

_REVIEW_WAIT_S = 10 * 60.0
_REVIEW_POLL_S = 3.0

# Engine factory seam: production resolves the routed engine from ctx;
# tests replace this wholesale.
EngineFactory = Callable[[OperationContext], ApplyEngine]


def _default_engine_factory(ctx: OperationContext) -> ApplyEngine:
    if _dev_enabled():
        script = ctx.input_snapshot.get("dev_engine_script")
        if isinstance(script, list) and script:
            return FakeApplyEngine([str(s) for s in script])
    if ctx.engine is None:
        raise EngineNotConfiguredError("apply")
    return ctx.engine.engine


ENGINE_FACTORY: EngineFactory = _default_engine_factory


def _dev_enabled() -> bool:
    return os.environ.get("FYJ_APPLY_DEV") == "1"


def apply_entrypoints() -> dict[str, Any]:
    return {"apply": _apply}


def _apply(ctx: OperationContext) -> OperationOutcome:
    if ctx.db is None or ctx.operation_id is None:
        raise RuntimeError("apply requires db + operation_id")
    application_id = str(ctx.input_snapshot.get("application_id", ""))
    if not application_id:
        raise ValueError("apply requires application_id")

    control = APPLY_CONTROL.setdefault(ctx.operation_id, ApplyControl())
    # F-M7: a runner-level cancel can land in the window between dispatch and
    # this control being registered — carry it into the ApplyControl so the
    # run finalizes as cancelled instead of proceeding under a stale request.
    if ctx.cancelled is not None and ctx.cancelled():
        control.cancel()
    # Every run shares 1 window, so the coroutine runs on the window's loop
    # (applier/window.md section 4.1).
    window = _window(ctx)
    try:
        return window.run(_apply_async(ctx, application_id, control, window))
    finally:
        APPLY_CONTROL.pop(ctx.operation_id, None)


def _window(ctx: OperationContext) -> ApplierWindow:
    """The shared window. Headless is a dev knob, for tests."""
    headed = not (_dev_enabled() and ctx.input_snapshot.get("dev_headed") is False)
    return window_for(headless=not headed)


def _publish(ctx: OperationContext, run_id: str, event: ApplyEvent) -> None:
    if ctx.publish is None:
        return
    ctx.publish(
        make_event(
            "apply",
            {
                "run_id": run_id,
                "operation_id": ctx.operation_id,
                "event": event.type.value,
                **event.data,
            },
        )
    )


def has_extracted_profile(repos: Any) -> bool:
    """Whether a master resume exists and its facts were read or typed at all."""
    profile = repos.profile.get_current()
    return profile is not None and isinstance(profile.application_profile, dict)


def has_application_profile(repos: Any) -> bool:
    """Whether the user has confirmed the facts the Applier fills forms from: read
    from the resume or typed, then saved once in Applicant details (audit A5)."""
    from .applicant import details_confirmed

    profile = repos.profile.get_current()
    return profile is not None and details_confirmed(profile.application_profile)


def _run_dir(run_id: str) -> Path:
    return resolve_data_dir() / "apply_runs" / run_id


def purge_run_dirs(run_ids: Iterable[str]) -> None:
    """F-M8: remove `apply_runs/<run_id>/` dirs when their rows are purged. Best
    effort: a failure is only logged. A target must resolve strictly inside the
    `apply_runs` base. Blocking; use `asyncio.to_thread` from async paths."""
    log = logging.getLogger("fyj.sidecar")
    base = (resolve_data_dir() / "apply_runs").resolve()
    for run_id in run_ids:
        target = (base / run_id).resolve()
        if target == base or base not in target.parents:
            log.warning("apply-run dir purge refused for %r: outside %s", run_id, base)
            continue
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            continue  # never materialized (run failed before its first write)
        except OSError as exc:
            log.warning("apply-run dir purge failed for %s: %s", target, exc)


async def _apply_async(
    ctx: OperationContext,
    application_id: str,
    control: ApplyControl,
    window: ApplierWindow | None = None,
) -> OperationOutcome:
    db = ctx.db
    assert db is not None
    if window is None:
        window = _window(ctx)
    if not window.on_loop():
        return await window.run_async(_apply_async(ctx, application_id, control, window))

    # -- load the card + create/adopt the durable run -------------------------
    with db.repos() as repos:
        app_row = repos.applications.get(application_id)
        if app_row is None:
            raise ValueError(f"application {application_id!r} not found")
        job = repos.jobs.get(app_row.job_id)
        if job is None:
            raise ValueError(f"job {app_row.job_id!r} not found")
        # Before the run row: a run against a job with no link has no form and would
        # sit in the panel unactionable (url-less-jobs.md section 5.4). This is the retry
        # path's guard; `start_apply` refuses first. Same dev seam as the route.
        dev_form_url = str(ctx.input_snapshot.get("dev_form_url") or "")
        if not job.canonical_url and not dev_form_url:
            raise ValueError(NO_POSTING_URL_DETAIL)
        if not has_application_profile(repos):
            raise ValueError(NO_APPLICATION_PROFILE_DETAIL)
        # Adopt the route-created row by its snapshot id first: the route
        # commits it BEFORE runner.submit, so it is always visible here, while
        # `operation_id` may not be attached yet (the route writes that after
        # submit — racing it minted duplicate runs, 2026-07-25).
        run = None
        snap_run_id = str(ctx.input_snapshot.get("run_id") or "")
        if snap_run_id:
            run = repos.apply_runs.get(snap_run_id)
        if run is None:
            run = repos.apply_runs.get_by_operation(ctx.operation_id or "")
        if run is None:
            run = repos.apply_runs.create(
                application_id,
                operation_id=ctx.operation_id,
                retry_of_run_id=ctx.input_snapshot.get("retry_of_run_id"),
                source_url=job.canonical_url or dev_form_url,
                deadline_at=now_utc() + timedelta(minutes=20),
            )
        elif run.operation_id != ctx.operation_id:
            run = repos.apply_runs.update(run.id, operation_id=ctx.operation_id)
        run_id = run.id
        job_url = job.canonical_url or dev_form_url
        company = job.company
        role = job.title
        job_location = job.location or ""
        jd_text = (job.description or "")[:12_000]

    # Late-failure finalize (F-M3): any exception after adoption (packet wait, PDF
    # render, browser launch, `run_apply`) must land the run row terminal before the
    # op fails, or the panel shows a live status forever. A user cancel lands
    # `interrupted`; both finalizers are guarded to active rows.
    try:
        # -- wait for the packet, freeze the exact artifacts (section 8.1) ----------
        resume_md, resume_label, resume_artifact_id = await _wait_for_packet(
            ctx, application_id, run_id, control
        )
        if control.cancelled:
            # Raise CompletionCancelled, which the runner maps to `cancelled`; returning
            # normally marked a user's Stop as "Succeeded". The row lands `interrupted` first;
            # the handler below re-enters it as a guarded no-op.
            _finalize_cancel(ctx, run_id)
            raise CompletionCancelled("cancelled by the user")

        with db.repos() as repos:
            from .applicant import apply_facts

            profile = repos.profile.get_current()
            facts = apply_facts(
                profile.application_profile if profile is not None else None, job_location
            )
            prefs_row = repos.preferences.get_or_create()
            ui_state = prefs_row.ui_state or {}
            preferences = {
                str(k): str(v)
                for k, v in (ui_state.get("apply_preferences") or {}).items()
            }
            repos.apply_runs.update(
                run_id,
                status="running",
                phase="opening",
                resume_artifact_id=resume_artifact_id,
            )

        run_dir = _run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        resume_pdf = run_dir / "resume.pdf"

        dev = _dev_enabled()
        allow_local = bool(dev and ctx.input_snapshot.get("dev_allow_local"))
        review_wait_s = _REVIEW_WAIT_S
        if dev and ctx.input_snapshot.get("dev_review_wait_s") is not None:
            review_wait_s = float(ctx.input_snapshot["dev_review_wait_s"])

        engine = ENGINE_FACTORY(ctx)

        # The tab first: it holds the window open while the PDF renders on the
        # window's Playwright (window.md section 4.4).
        try:
            page = await window.open_tab(run_id)
        except Exception as exc:
            if is_browser_missing(str(exc)):
                raise BrowserMissingError(BROWSER_MISSING_DETAIL) from exc
            raise
        try:
            try:
                await render_resume_pdf_async(
                    await window.playwright(), resume_md, str(resume_pdf)
                )
            except Exception as exc:
                if is_browser_missing(str(exc)):
                    raise BrowserMissingError(BROWSER_MISSING_DETAIL) from exc
                raise

            request = ApplyRequest(
                run_id=run_id,
                application_id=application_id,
                job_url=job_url,
                company=company,
                role=role,
                jd_text=jd_text,
                profile_facts=facts,
                preferences=preferences,
                approved_links=(),
                artifacts=(
                    ArtifactRef(
                        artifact_id="resume-pdf",
                        label=f"{resume_label} (PDF)",
                        path=str(resume_pdf),
                        kind="resume",
                    ),
                ),
                resume_label=resume_label,
                screenshot_dir=str(run_dir / "screenshots"),
            )

            def on_event(event: ApplyEvent) -> None:
                _publish(ctx, run_id, event)
                _persist_event(ctx, run_id, event)

            result = await run_apply(
                page,
                request,
                engine,
                on_event,
                control,
                policy=UrlPolicy(allow_local=allow_local),
            )
            result = await _review_window(ctx, run_id, page, result, review_wait_s, control)
        finally:
            try:
                await window.close_tab(run_id)
            except Exception:  # noqa: S110 — already closed by the user
                logging.getLogger("fyj.sidecar").debug("tab close raced", exc_info=True)

        return _finalize(ctx, application_id, run_id, result)
    except Exception as exc:
        # The run-row finalize is best-effort relative to the ORIGINAL failure:
        # if the finalize itself raises (a transient DB error), that must NOT
        # replace `exc` as what propagates — the runner captures `exc` verbatim
        # into the ledger (NFR-SIDE-04), and a swapped-in finalize error would
        # corrupt that capture (2026-07-25).
        try:
            if control.cancelled:
                _finalize_cancel(ctx, run_id)
            elif isinstance(exc, BrowserMissingError):
                # Coded, so the panel offers Download instead of raw text (A18).
                finalize_run_failed(
                    db,
                    run_id,
                    str(exc),
                    blockers=[
                        {"kind": "browser_missing", "detail": str(exc), "field_label": ""}
                    ],
                )
            else:
                finalize_run_failed(db, run_id, f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001 — never let a finalize failure mask the real error
            logging.getLogger("fyj.sidecar").exception(
                "apply-run finalize failed for %s while handling %s; "
                "propagating the ORIGINAL error",
                run_id,
                type(exc).__name__,
            )
        raise


async def _wait_for_packet(
    ctx: OperationContext, application_id: str, run_id: str, control: ApplyControl
) -> tuple[str, str, str | None]:
    """Resolve the resume to use, waiting for an in-flight tailored artifact
    (section 8.1). Returns (markdown, honesty label, artifact_id | None)."""
    db = ctx.db
    assert db is not None
    waited = 0.0
    announced = False
    while True:
        with db.repos() as repos:
            heads = [
                a
                for a in repos.artifacts.list_for_application(application_id)
                if a.kind == "tailored_resume" and a.superseded_by is None
            ]
            head = heads[-1] if heads else None
            if head is not None and head.markdown:
                return head.markdown, "tailored resume", head.id
            pending = False
            if head is not None and head.operation_id:
                op = repos.operations.get(head.operation_id)
                pending = op is not None and op.state in OP_ACTIVE_STATES
            if not pending:
                profile = repos.profile.get_current()
                if profile is None or not profile.resume_markdown:
                    raise ValueError("no resume available: save a master profile first")
                return profile.resume_markdown, "master resume", None
            if not announced:
                repos.apply_runs.update(run_id, status="waiting_for_packet")
                announced = True
        if ctx.publish is not None:
            ctx.publish(
                make_event(
                    "apply",
                    {
                        "run_id": run_id,
                        "operation_id": ctx.operation_id,
                        "event": "apply.waiting_for_packet",
                    },
                )
            )
        if control.cancelled:
            # Discarded by the caller (it finalizes as cancelled immediately).
            return "", "master resume", None
        if waited >= _PACKET_WAIT_MAX_S:
            # F-M3: the packet never arrived — fall back to the REAL master
            # resume exactly like the no-pending branch above; never hand the
            # agent a blank attachment whose label claims to be a resume.
            with db.repos() as repos:
                profile = repos.profile.get_current()
                if profile is None or not profile.resume_markdown:
                    raise ValueError(
                        "tailored resume never arrived within "
                        f"{int(_PACKET_WAIT_MAX_S)}s and no master resume is saved"
                    )
                return profile.resume_markdown, "master resume", None
        await asyncio.sleep(_PACKET_POLL_S)
        waited += _PACKET_POLL_S


def _persist_event(ctx: OperationContext, run_id: str, event: ApplyEvent) -> None:
    """Fold progress into the durable run row (state-first, then SSE section 9.2)."""
    db = ctx.db
    assert db is not None
    fields: dict[str, Any] = {}
    if event.type.value == "apply.phase_changed":
        fields["phase"] = str(event.data.get("phase", ""))
    elif event.type.value == "apply.screenshot_ready":
        path = str(event.data.get("path", ""))
        if path:
            with db.repos() as repos:
                run = repos.apply_runs.get(run_id)
                if run is not None:
                    shots = list(run.screenshots)
                    shots.append(path)
                    repos.apply_runs.update(run_id, screenshots=shots)
        return
    if fields:
        with db.repos() as repos:
            repos.apply_runs.update(run_id, **fields)


async def _run_user_submit(ctx: OperationContext, run_id: str, page: Any) -> None:
    """Click Submit once, because the USER asked (S-A5). The agent cannot reach
    this: `submit_application` sits outside the model's tool vocabulary and the
    only caller is the request the human made from the review surface. The
    outcome is recorded verbatim, including a failure, and the confirmation
    poll below keeps running either way."""
    outcome = await submit_application(page)
    db = ctx.db
    assert db is not None
    with db.repos() as repos:
        fields: dict[str, Any] = {"summary": outcome.note}
        if outcome.final_url:
            fields["final_url"] = outcome.final_url
        if outcome.confirmed:
            fields["status"] = "submitted"
            fields["submit_evidence"] = "user_submitted"
        repos.apply_runs.update(run_id, **fields)
        if outcome.confirmed:
            run = repos.apply_runs.get(run_id)
            if run is not None:
                advance_card_to_applied(repos, run.application_id, by="user_submitted")
    if ctx.publish is not None:
        ctx.publish(
            make_event(
                "apply",
                {
                    "run_id": run_id,
                    "operation_id": ctx.operation_id,
                    "event": "apply.user_submitted",
                    "clicked": outcome.clicked,
                    "confirmed": outcome.confirmed,
                    "note": outcome.note,
                },
            )
        )


async def _review_window(
    ctx: OperationContext,
    run_id: str,
    page: Any,
    result: ApplyResult,
    review_wait_s: float,
    control: ApplyControl,
) -> ApplyResult:
    """section 8.4: after ready_for_human, hold the browser open and watch for a
    machine-detectable confirmation while the human reviews and submits."""
    if result.status is not ApplyStatus.READY_FOR_HUMAN or review_wait_s <= 0:
        return result
    waited = 0.0
    while waited < review_wait_s and not control.cancelled:
        await asyncio.sleep(_REVIEW_POLL_S)
        waited += _REVIEW_POLL_S
        if page.is_closed():
            return result  # user closed after reviewing; run stays ready_for_human
        if control.take_submit_request():
            await _run_user_submit(ctx, run_id, page)
            continue
        try:
            obs = await observe(page)
        except Exception:
            return result
        if PageState.CONFIRMATION in classify(obs):
            shot = _run_dir(run_id) / "screenshots" / "post-submit.png"
            try:
                await page.screenshot(path=str(shot))
            except Exception:
                shot = None  # type: ignore[assignment]
            db = ctx.db
            assert db is not None
            with db.repos() as repos:
                repos.apply_runs.update(
                    run_id,
                    submit_evidence="confirmation_detected",
                    final_url=obs.url,
                )
                if shot is not None:
                    run = repos.apply_runs.get(run_id)
                    if run is not None:
                        shots = list(run.screenshots) + [str(shot)]
                        repos.apply_runs.update(run_id, screenshots=shots)
            if ctx.publish is not None:
                ctx.publish(
                    make_event(
                        "apply",
                        {
                            "run_id": run_id,
                            "operation_id": ctx.operation_id,
                            "event": "apply.confirmation_detected",
                        },
                    )
                )
            return result
    return result


# `APPLY_RUN_ACTIVE_STATUSES` (db/models.py) is the set of statuses a live run
# holds before it lands terminal — the only states a late-failure/cancel
# finalize below may overwrite (double-finalize impossible: a row that already
# landed terminal is left untouched).


def finalize_run_failed(
    db: Database, run_id: str, summary: str, *, blockers: list[dict[str, str]] | None = None
) -> None:
    """Land the run row `failed` when the entrypoint dies before `_finalize` owns it,
    or the route's enqueue fails. Guarded to active rows; the op failure itself is
    the runner's job. This keeps the run from sitting live forever."""
    with db.repos() as repos:
        run = repos.apply_runs.get(run_id)
        if run is None or run.status not in APPLY_RUN_ACTIVE_STATUSES:
            return
        fields: dict[str, Any] = {}
        if blockers is not None:
            fields["blockers"] = blockers
        repos.apply_runs.update(
            run_id,
            status="failed",
            phase="failed",
            summary=summary,
            ended_at=now_utc(),
            **fields,
        )


def finalize_run_interrupted(db: Database, run_id: str, summary: str) -> None:
    """Land the run row `interrupted` when a queued `apply` op is cancelled before
    its entrypoint runs; otherwise it stays active and Apply keeps returning it.
    Guarded to active rows, so a no-op if the entrypoint finalized it first."""
    with db.repos() as repos:
        run = repos.apply_runs.get(run_id)
        if run is None or run.status not in APPLY_RUN_ACTIVE_STATUSES:
            return
        repos.apply_runs.update(
            run_id,
            status="interrupted",
            phase="interrupted",
            summary=summary,
            ended_at=now_utc(),
        )


def advance_card_to_applied(repos: Repos, application_id: str, *, by: str) -> None:
    """Move a pre-submission card to Applied and record it (section 8.4), the 1
    implementation (D-A7): the applier's detection and the user's attestation differ
    only in who's credited. A card past Seeking Referral is never dragged back."""
    app_row = repos.applications.get(application_id)
    if app_row is None or app_row.column not in ("saved", "seeking_referral"):
        return
    repos.applications.update(application_id, column="applied", applied_via="applier")
    repos.application_events.create(
        application_id,
        "column_change",
        {"from": app_row.column, "to": "applied", "by": by},
    )


def _finalize_cancel(ctx: OperationContext, run_id: str) -> None:
    """Land the durable run `interrupted`. Guarded to ACTIVE rows upstream, so
    calling it twice on one run is a no-op."""
    db = ctx.db
    assert db is not None
    finalize_run_interrupted(db, run_id, "cancelled by the user")


def _finalize(
    ctx: OperationContext,
    application_id: str,
    run_id: str,
    result: ApplyResult,
) -> OperationOutcome:
    db = ctx.db
    assert db is not None
    with db.repos() as repos:
        run = repos.apply_runs.get(run_id)
        confirmed = run is not None and run.submit_evidence == "confirmation_detected"
        status = "submitted" if confirmed else result.status.value
        repos.apply_runs.update(
            run_id,
            status=status,
            phase=result.status.value if not confirmed else "ready_for_human",
            final_url=result.final_url or (run.final_url if run else ""),
            summary=result.summary,
            blockers=[
                {"kind": b.kind, "detail": b.detail, "field_label": b.field_label}
                for b in result.blockers
            ],
            fields=[
                {"label": f.label, "action": f.action, "ok": f.ok, "note": f.note}
                for f in result.fields
            ],
            screenshots=list(result.screenshots),
            usage={
                "calls": result.usage.calls,
                "tokens_in": result.usage.tokens_in,
                "tokens_out": result.usage.tokens_out,
                "cost_usd": result.usage.cost_usd,
            },
            steps=result.steps,
            ended_at=now_utc(),
        )
        if confirmed:
            advance_card_to_applied(repos, application_id, by="applier")
    usage = {
        "tokens_in": result.usage.tokens_in,
        "tokens_out": result.usage.tokens_out,
        "usd": result.usage.cost_usd,
        "calls": result.usage.calls,
    }
    return OperationOutcome(
        result_ref={"run_id": run_id, "status": result.status.value},
        usage=usage,
        # A run that ended anywhere but `ready_for_human` didn't do what the user asked,
        # and the ledger says so: a `blocked` run once read "Succeeded" beside a panel
        # reading "Blocked". The reason is the first blocker, verbatim; usage rides along,
        # so a failed run keeps its cost.
        error=None if status in ("ready_for_human", "submitted") else _failure_reason(result),
    )


def _failure_reason(result: ApplyResult) -> str:
    """The verbatim line for a run that did not reach a human-reviewable form."""
    for blocker in result.blockers:
        detail = blocker.detail or blocker.kind
        return f"{blocker.kind}: {detail}" if blocker.detail else blocker.kind
    return result.summary or f"apply run ended {result.status.value}"
