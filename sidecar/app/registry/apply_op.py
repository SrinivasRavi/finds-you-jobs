"""The Applier operation (docs/internal/applier.md §8) — one direct `apply`
op from the Tracker, driving the jobapplier package's agent loop.

Lifecycle (§8.1): the route creates the durable ApplyRun immediately; this
entrypoint waits for the packet (a still-generating tailored resume) if
needed, freezes the exact artifact used, renders it to PDF, opens the
browser, and hands off to `jobapplier.run_apply`. The loop cannot submit —
its tool vocabulary has no submit tool (§4.2).

P1 handoff (§8.4): on `ready_for_human` the HEADED browser stays open for a
bounded review window while we watch for a confirmation page. A detected
confirmation records `submitted` with `submit_evidence=confirmation_detected`
and moves the card to Applied; otherwise the run stays `ready_for_human` and
the user may attest via the API. Closing the browser is an interruption,
never a success.

**Test seam.** `ENGINE_FACTORY` builds the model engine (tests inject
`FakeApplyEngine`); `FYJ_APPLY_DEV=1` additionally honors the dev knobs in
`input_snapshot` (`dev_engine_script`, `dev_allow_local`, `dev_headed=false`,
`dev_review_wait_s`) so e2e can drive a real run against a local fixture form
with zero model calls and zero external traffic. Without that env the knobs
are ignored.
"""

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
)
from sidecar.packages.jobapplier.fake import FakeApplyEngine
from sidecar.packages.jobapplier.loop import ApplyEngine

from ..db import Repos
from ..db.base import now_utc
from ..db.database import Database, resolve_data_dir
from ..events import make_event
from .engines import EngineNotConfiguredError
from .operations import OperationContext, OperationOutcome
from .pdf import render_resume_pdf_async

# Cancel plumbing: the cancel route flips the control for a live operation.
APPLY_CONTROL: dict[str, ApplyControl] = {}

_PACKET_POLL_S = 2.0
_PACKET_WAIT_MAX_S = 15 * 60.0
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
    try:
        return asyncio.run(_apply_async(ctx, application_id, control))
    finally:
        APPLY_CONTROL.pop(ctx.operation_id, None)


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


def _run_dir(run_id: str) -> Path:
    return resolve_data_dir() / "apply_runs" / run_id


def purge_run_dirs(run_ids: Iterable[str]) -> None:
    """F-M8: remove `apply_runs/<run_id>/` artifact dirs (frozen resume PDF +
    per-step PNGs) when their rows are purged — card delete and retention purge
    both call this, or the evidence outlives the record forever.

    Best-effort by design: a missing dir is fine, and an unremovable one must
    never fail the delete/purge that triggered it — it is only logged. Path
    safety: a target is removed only when its resolved path is a strict child
    of the resolved `apply_runs` base (a hostile/corrupt run id can never walk
    out of it). Blocking (shutil.rmtree) — call via `asyncio.to_thread` from
    async request paths."""
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
    ctx: OperationContext, application_id: str, control: ApplyControl
) -> OperationOutcome:
    db = ctx.db
    assert db is not None

    # -- load the card + create/adopt the durable run -------------------------
    with db.repos() as repos:
        app_row = repos.applications.get(application_id)
        if app_row is None:
            raise ValueError(f"application {application_id!r} not found")
        job = repos.jobs.get(app_row.job_id)
        if job is None:
            raise ValueError(f"job {app_row.job_id!r} not found")
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
                source_url=job.canonical_url,
                deadline_at=now_utc() + timedelta(minutes=20),
            )
        elif run.operation_id != ctx.operation_id:
            run = repos.apply_runs.update(run.id, operation_id=ctx.operation_id)
        run_id = run.id
        job_url = job.canonical_url
        company = job.company
        role = job.title
        jd_text = (job.description or "")[:12_000]

    # Late-failure finalize (F-M3 follow-up, extended 2026-07-25): ANY
    # exception escaping the post-adoption body below — the packet wait (which
    # may raise AFTER setting the row `waiting_for_packet`), the PDF render,
    # the browser launch, `run_apply` itself — must land the durable run row
    # terminal before the op fails, or the Applier panel shows a live status
    # forever (until boot sweep). The runner still fails the *operation*
    # verbatim; a user cancel lands `interrupted`, never `failed`; both
    # finalizers are guarded to ACTIVE rows, so a row `_finalize` already
    # landed is never re-written.
    try:
        # -- wait for the packet, freeze the exact artifacts (§8.1) ----------
        resume_md, resume_label, resume_artifact_id = await _wait_for_packet(
            ctx, application_id, run_id, control
        )
        if control.cancelled:
            return _finalize_cancel(ctx, run_id)

        with db.repos() as repos:
            profile = repos.profile.get_current()
            facts: dict[str, str] = {}
            if profile is not None and isinstance(profile.application_profile, dict):
                facts = {
                    str(k): str(v)
                    for k, v in profile.application_profile.items()
                    if isinstance(v, str | int | float) and str(v)
                }
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
        headed = not (dev and ctx.input_snapshot.get("dev_headed") is False)
        review_wait_s = _REVIEW_WAIT_S
        if dev and ctx.input_snapshot.get("dev_review_wait_s") is not None:
            review_wait_s = float(ctx.input_snapshot["dev_review_wait_s"])

        engine = ENGINE_FACTORY(ctx)

        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            await render_resume_pdf_async(pw, resume_md, str(resume_pdf))

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

            browser = await pw.chromium.launch(headless=not headed)
            page = await browser.new_page()

            def on_event(event: ApplyEvent) -> None:
                _publish(ctx, run_id, event)
                _persist_event(ctx, run_id, event)

            try:
                result = await run_apply(
                    page,
                    request,
                    engine,
                    on_event,
                    control,
                    policy=UrlPolicy(allow_local=allow_local),
                )
                result = await _review_window(
                    ctx, run_id, page, result, review_wait_s, control
                )
            finally:
                try:
                    await browser.close()
                except Exception:  # noqa: S110 — already closed by the user
                    logging.getLogger("fyj.sidecar").debug(
                        "browser close raced", exc_info=True
                    )

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
    (§8.1). Returns (markdown, honesty label, artifact_id | None)."""
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
                pending = op is not None and op.state in ("queued", "running")
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
    """Fold progress into the durable run row (state-first, then SSE §9.2)."""
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


async def _review_window(
    ctx: OperationContext,
    run_id: str,
    page: Any,
    result: ApplyResult,
    review_wait_s: float,
    control: ApplyControl,
) -> ApplyResult:
    """§8.4: after ready_for_human, hold the browser open and watch for a
    machine-detectable confirmation while the human reviews and submits."""
    if result.status is not ApplyStatus.READY_FOR_HUMAN or review_wait_s <= 0:
        return result
    waited = 0.0
    while waited < review_wait_s and not control.cancelled:
        await asyncio.sleep(_REVIEW_POLL_S)
        waited += _REVIEW_POLL_S
        if page.is_closed():
            return result  # user closed after reviewing; run stays ready_for_human
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


# Statuses a live run holds before it lands terminal — the only states a
# late-failure/cancel finalize may overwrite (double-finalize impossible:
# a row that already landed terminal is left untouched).
_ACTIVE_RUN_STATUSES = ("queued", "waiting_for_packet", "running")


def finalize_run_failed(db: Database, run_id: str, summary: str) -> None:
    """Land the run row terminal (`failed`) when the entrypoint dies before
    `_finalize` owns the row — or when the route's enqueue itself fails (the
    start_apply strand, 2026-07-25). Mirrors `_finalize_cancel`'s mechanics,
    guarded to ACTIVE rows only. The OPERATION failure (row + ledger + SSE) is
    the runner's job; this only keeps the durable run from sitting in a live
    status forever."""
    with db.repos() as repos:
        run = repos.apply_runs.get(run_id)
        if run is None or run.status not in _ACTIVE_RUN_STATUSES:
            return
        repos.apply_runs.update(
            run_id,
            status="failed",
            phase="failed",
            summary=summary,
            ended_at=now_utc(),
        )


def finalize_run_interrupted(db: Database, run_id: str, summary: str) -> None:
    """Land the run row terminal (`interrupted`) when a QUEUED `apply` op is
    cancelled before its entrypoint ever runs (the start_apply single-flight
    strand, 2026-07-25): the generic operations-cancel route marks the queued
    op `cancelled`, but nothing else finalizes the durable run — it would sit
    in an ACTIVE status forever, so `start_apply`'s single-flight would keep
    returning the dead run on every later Apply click for that card. A user
    cancel lands `interrupted`, never `failed`. Mirrors `finalize_run_failed`'s
    mechanics, guarded to ACTIVE rows only (double-finalize impossible: a row
    that already landed terminal is left untouched — so this is a no-op if the
    entrypoint raced ahead and finalized the run itself)."""
    with db.repos() as repos:
        run = repos.apply_runs.get(run_id)
        if run is None or run.status not in _ACTIVE_RUN_STATUSES:
            return
        repos.apply_runs.update(
            run_id,
            status="interrupted",
            phase="interrupted",
            summary=summary,
            ended_at=now_utc(),
        )


def advance_card_to_applied(repos: Repos, application_id: str, *, by: str) -> None:
    """Move a pre-submission card to Applied and record the move (§8.4).

    The ONE implementation of the transition (D-A7): the applier's own
    confirmation detection (`by="applier"`) and the human's attestation
    (`by="user_attested"`) differ only in who is credited in the event detail —
    they must never diverge in *what* they write. A card already past
    Saved/Seeking Referral is left alone (never dragged backward)."""
    app_row = repos.applications.get(application_id)
    if app_row is None or app_row.column not in ("saved", "seeking_referral"):
        return
    repos.applications.update(application_id, column="applied", applied_via="applier")
    repos.application_events.create(
        application_id,
        "column_change",
        {"from": app_row.column, "to": "applied", "by": by},
    )


def _finalize_cancel(ctx: OperationContext, run_id: str) -> OperationOutcome:
    db = ctx.db
    assert db is not None
    finalize_run_interrupted(db, run_id, "cancelled by the user")
    return OperationOutcome(result_ref={"run_id": run_id, "status": "interrupted"})


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
    )
