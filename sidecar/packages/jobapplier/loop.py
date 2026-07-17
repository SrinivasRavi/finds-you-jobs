# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""The apply agent loop (docs/internal/applier.md §4/§5): observe → decide →
execute → verify → repeat, under a hard time budget and a no-progress budget.

Terminal honesty (§8.4): P1 success is ``ready_for_human`` — the browser
stays open (the caller owns its lifetime), the human reviews and submits.
The loop cannot submit: there is no submit tool in the vocabulary, and the
executor would reject one anyway.

The engine is the app's text-completion seam (sync ``complete``); each call
runs in a worker thread so the browser's event loop keeps breathing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Protocol

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .actions import parse_action
from .classifier import classify
from .executor import Executor, UrlPolicy
from .observe import Observation, observe
from .prompt import render_turn, system_prompt
from .types import (
    ApplyControl,
    ApplyError,
    ApplyEvent,
    ApplyEventSink,
    ApplyEventType,
    ApplyPhase,
    ApplyRequest,
    ApplyResult,
    ApplyStatus,
    Blocker,
    DisallowedActionError,
    FieldOutcome,
    PageState,
    StaleElementError,
    Usage,
)

logger = logging.getLogger("fyj.jobapplier")


class ApplyEngine(Protocol):
    """The model seam — same shape as the app's Engine protocol."""

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Any]: ...


_NO_PROGRESS_OBSERVATIONS = 3  # materially identical observations in a row
_MAX_CONSECUTIVE_FAILURES = 4  # failed/disallowed actions in a row
_HARD_WALLS = {
    PageState.POSTING_CLOSED: "posting_closed",
    PageState.CAPTCHA_OR_ANTI_BOT: "captcha",
    PageState.LOGIN_WALL: "login_wall",
}
_MUTATING_TOOLS = {"click", "navigate", "fill", "select", "check", "upload_artifact"}
_FIELD_TOOLS = {"fill", "select", "check", "upload_artifact"}


async def run_apply(
    page: Page,
    request: ApplyRequest,
    engine: ApplyEngine,
    on_event: ApplyEventSink,
    control: ApplyControl,
    *,
    policy: UrlPolicy | None = None,
) -> ApplyResult:
    """Run one apply attempt on an already-open page. The caller owns the
    browser context (and keeps it open on ``ready_for_human``, §8.4)."""
    runner = _Run(page, request, engine, on_event, control, policy or UrlPolicy())
    return await runner.run()


class _Run:
    def __init__(
        self,
        page: Page,
        request: ApplyRequest,
        engine: ApplyEngine,
        on_event: ApplyEventSink,
        control: ApplyControl,
        policy: UrlPolicy,
    ) -> None:
        self._page = page
        self._request = request
        self._engine = engine
        self._emit = on_event
        self._control = control
        self._policy = policy
        self._executor = Executor(page, request, policy)
        self._started = time.monotonic()
        self._history: list[str] = []
        self._fields: dict[str, FieldOutcome] = {}
        self._blockers: list[Blocker] = []
        self._screenshots: list[str] = []
        self._usage_calls = 0
        self._usage_in = 0
        self._usage_out = 0
        self._cost_usd = 0.0
        self._cost_known = False
        self._steps = 0
        self._phase: ApplyPhase | None = None
        self._form_seen = False

    # -- plumbing -------------------------------------------------------------

    def _remaining(self) -> float:
        return self._request.deadline_s - (time.monotonic() - self._started)

    def _set_phase(self, phase: ApplyPhase) -> None:
        if phase is self._phase:
            return
        self._phase = phase
        self._emit(ApplyEvent(ApplyEventType.PHASE_CHANGED, {"phase": phase.value}))

    async def _observe(self) -> Observation:
        obs = await observe(self._page)
        self._executor.bind_observation(obs)
        self._emit(
            ApplyEvent(
                ApplyEventType.OBSERVED,
                {
                    "url": obs.url,
                    "title": obs.title,
                    "elements": len(obs.elements),
                    "states": sorted(s.value for s in classify(obs)),
                },
            )
        )
        return obs

    async def _screenshot(self, tag: str) -> None:
        if not self._request.screenshot_dir:
            return
        try:
            path = Path(self._request.screenshot_dir) / f"{self._steps:03d}-{tag}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            await self._page.screenshot(path=str(path))
            self._screenshots.append(str(path))
            self._emit(
                ApplyEvent(ApplyEventType.SCREENSHOT_READY, {"path": str(path)})
            )
        except PlaywrightError:  # evidence must never kill the run
            logger.warning("screenshot %s failed", tag, exc_info=True)

    def _decide(self, obs: Observation) -> str:
        reply, usage = self._engine.complete(
            system_prompt(),
            render_turn(self._request, obs, self._history, self._remaining()),
        )
        self._usage_calls += 1
        tokens_in = getattr(usage, "tokens_in", None)
        tokens_out = getattr(usage, "tokens_out", None)
        cost = getattr(usage, "cost_usd", None)
        if tokens_in:
            self._usage_in += int(tokens_in)
        if tokens_out:
            self._usage_out += int(tokens_out)
        if cost is not None:
            self._cost_usd += float(cost)
            self._cost_known = True
        return reply

    def _usage(self) -> Usage:
        return Usage(
            calls=self._usage_calls,
            tokens_in=self._usage_in,
            tokens_out=self._usage_out,
            cost_usd=self._cost_usd if self._cost_known else None,
        )

    def _result(
        self, status: ApplyStatus, summary: str, obs: Observation | None
    ) -> ApplyResult:
        states: tuple[PageState, ...] = ()
        if obs is not None:
            states = tuple(sorted(classify(obs), key=lambda s: s.value))
        self._emit(
            ApplyEvent(
                ApplyEventType.COMPLETED,
                {"status": status.value, "summary": summary},
            )
        )
        return ApplyResult(
            run_id=self._request.run_id,
            status=status,
            final_url=self._page.url if not self._page.is_closed() else "",
            summary=summary,
            page_states=states,
            fields=tuple(self._fields.values()),
            blockers=tuple(self._blockers),
            screenshots=tuple(self._screenshots),
            usage=self._usage(),
            steps=self._steps,
        )

    def _blocked(
        self, kind: str, detail: str, obs: Observation | None
    ) -> ApplyResult:
        blocker = Blocker(kind=kind, detail=detail)
        self._blockers.append(blocker)
        self._emit(
            ApplyEvent(
                ApplyEventType.BLOCKER_FOUND, {"kind": kind, "detail": detail}
            )
        )
        self._set_phase(ApplyPhase.BLOCKED)
        return self._result(ApplyStatus.BLOCKED, detail, obs)

    # -- the loop --------------------------------------------------------------

    async def run(self) -> ApplyResult:
        try:
            return await self._run_inner()
        except PlaywrightError as exc:
            # The human closed the browser, or the page died mid-action. Not a
            # success, not a lie — an interruption (§8.3).
            self._emit(ApplyEvent(ApplyEventType.INTERRUPTED, {"reason": str(exc)}))
            self._set_phase(ApplyPhase.INTERRUPTED)
            return self._result(
                ApplyStatus.INTERRUPTED,
                "the browser closed or the page died before the run finished",
                None,
            )

    async def _run_inner(self) -> ApplyResult:
        self._set_phase(ApplyPhase.OPENING)
        refusal = self._policy.check(self._request.job_url)
        if refusal is not None:
            return self._blocked("error", f"job URL refused: {refusal}", None)
        await self._page.goto(
            self._request.job_url, timeout=30_000, wait_until="domcontentloaded"
        )
        obs = await self._observe()
        await self._screenshot("opened")
        self._set_phase(ApplyPhase.FINDING_FORM)

        identical_streak = 0
        failure_streak = 0
        last_digest = ""

        while True:
            if self._control.cancelled:
                self._set_phase(ApplyPhase.INTERRUPTED)
                return self._result(
                    ApplyStatus.INTERRUPTED, "cancelled by the user", obs
                )
            if self._remaining() <= 0:
                self._set_phase(ApplyPhase.TIMED_OUT)
                await self._screenshot("timeout")
                return self._result(
                    ApplyStatus.TIMED_OUT,
                    "the 20-minute apply budget ran out before the form was ready",
                    obs,
                )

            states = classify(obs)
            for wall_state, kind in _HARD_WALLS.items():
                if wall_state in states:
                    await self._screenshot(kind)
                    return self._blocked(
                        kind, f"stopped at {wall_state.value} — {kind}", obs
                    )
            if PageState.APPLICATION_FORM in states:
                self._form_seen = True
                if self._phase in (ApplyPhase.OPENING, ApplyPhase.FINDING_FORM):
                    self._set_phase(ApplyPhase.FILLING)
                    await self._screenshot("form-found")

            digest = hashlib.sha256(obs.element_tree_html.encode()).hexdigest()
            identical_streak = identical_streak + 1 if digest == last_digest else 0
            last_digest = digest
            if identical_streak >= _NO_PROGRESS_OBSERVATIONS:
                await self._screenshot("no-progress")
                return self._blocked(
                    "no_form" if not self._form_seen else "error",
                    "no progress: the page stopped changing across "
                    f"{_NO_PROGRESS_OBSERVATIONS} observations",
                    obs,
                )

            reply = await asyncio.to_thread(self._decide, obs)
            self._steps += 1

            try:
                action = parse_action(reply)
            except DisallowedActionError as exc:
                failure_streak += 1
                self._history.append(f"(rejected) {exc}")
                self._emit(
                    ApplyEvent(ApplyEventType.ACTION_FAILED, {"reason": str(exc)})
                )
                if failure_streak >= _MAX_CONSECUTIVE_FAILURES:
                    return self._blocked(
                        "error", "the model kept producing invalid actions", obs
                    )
                continue

            if action.tool == "finish":
                reason = str(action.args["reason"])
                if not self._form_seen:
                    # finish without goal evidence is not a success (§4.2).
                    return self._blocked(
                        "no_form",
                        f"agent finished without reaching a form: {reason}",
                        obs,
                    )
                self._set_phase(ApplyPhase.VERIFYING)
                obs = await self._observe()
                await self._screenshot("handoff")
                self._set_phase(ApplyPhase.READY_FOR_HUMAN)
                self._emit(
                    ApplyEvent(ApplyEventType.READY_FOR_HUMAN, {"reason": reason})
                )
                return self._result(ApplyStatus.READY_FOR_HUMAN, reason, obs)

            if action.tool == "report_blocked":
                blocker = Blocker(
                    kind=str(action.args["kind"]),
                    detail=str(action.args["detail"]),
                    field_label=(
                        str(action.args["field_label"])
                        if "field_label" in action.args
                        else None
                    ),
                )
                self._blockers.append(blocker)
                self._history.append(
                    f"report_blocked({blocker.kind}: {blocker.field_label or blocker.detail})"
                )
                self._emit(
                    ApplyEvent(
                        ApplyEventType.BLOCKER_FOUND,
                        {
                            "kind": blocker.kind,
                            "detail": blocker.detail,
                            "field_label": blocker.field_label,
                        },
                    )
                )
                failure_streak = 0
                continue  # keep filling the rest (§6)

            label = ""
            if "element_id" in action.args:
                try:
                    label = self._executor.resolve(
                        str(action.args["element_id"])
                    ).label
                except (StaleElementError, ApplyError):
                    label = str(action.args["element_id"])
            self._emit(
                ApplyEvent(
                    ApplyEventType.ACTION_STARTED,
                    {"tool": action.tool, "label": label},
                )
            )
            try:
                outcome = await self._executor.execute(action)
            except StaleElementError as exc:
                failure_streak += 1
                self._history.append(f"(stale) {action.tool}: {exc}")
                self._emit(
                    ApplyEvent(ApplyEventType.ACTION_FAILED, {"reason": str(exc)})
                )
                if failure_streak >= _MAX_CONSECUTIVE_FAILURES:
                    return self._blocked(
                        "error", "the model kept referencing stale elements", obs
                    )
                obs = await self._observe()
                continue

            self._history.append(
                f"{action.tool}({label or '-'}) → {'ok' if outcome.ok else 'FAILED'}"
                f"{': ' + outcome.note if outcome.note else ''}"
            )
            if action.tool in _FIELD_TOOLS and label:
                self._fields[label] = FieldOutcome(
                    label=label,
                    action=action.tool if action.tool != "upload_artifact" else "upload",
                    ok=outcome.ok,
                    note="" if outcome.ok else outcome.note,
                )
            event_type = (
                ApplyEventType.ACTION_VERIFIED if outcome.ok else ApplyEventType.ACTION_FAILED
            )
            self._emit(
                ApplyEvent(
                    event_type,
                    {"tool": action.tool, "label": label, "note": outcome.note},
                )
            )
            failure_streak = 0 if outcome.ok else failure_streak + 1
            if failure_streak >= _MAX_CONSECUTIVE_FAILURES:
                await self._screenshot("stuck")
                return self._blocked(
                    "error",
                    f"repeated action failures without new evidence: {outcome.note}",
                    obs,
                )

            if action.tool in _MUTATING_TOOLS:
                obs = await self._observe()
