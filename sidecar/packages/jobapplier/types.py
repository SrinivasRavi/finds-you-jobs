# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""Typed contract of the Applier package (docs/internal/applier.md §3.1).

The app talks to the agent ONLY through these types: an immutable
``ApplyRequest`` in, a durable ``ApplyResult`` out, ``ApplyEvent``s streamed
through a sink while the run is live. No DB session, FastAPI request, bearer
token, raw secret, or UI object crosses this boundary — and nothing in this
package can submit an application: the P1 tool vocabulary ends at
``finish``/``report_blocked`` (§4.2; the P2 ``submit`` tool does not exist
here).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Page-state classification (§5.1)
# ---------------------------------------------------------------------------


class PageState(StrEnum):
    """What the current observation looks like. A page may match several."""

    JOB_DESCRIPTION = "job_description"
    APPLICATION_FORM = "application_form"
    APPLY_LINK_OR_BUTTON = "apply_link_or_button"
    EXTERNAL_APPLICATION_LINK = "external_application_link"
    LOGIN_WALL = "login_wall"
    CAPTCHA_OR_ANTI_BOT = "captcha_or_anti_bot"
    POSTING_CLOSED = "posting_closed"
    CONFIRMATION = "confirmation"
    VALIDATION_ERROR = "validation_error"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Run request (§3.1) — immutable for the run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRef:
    """One user-approved artifact the agent may upload — nothing else can be.

    ``label`` is what the model sees ("tailored resume (PDF)"); ``path`` stays
    executor-side and is never shown to the model (§5.3).
    """

    artifact_id: str
    label: str
    path: str
    kind: str  # resume | cover_letter


@dataclass(frozen=True)
class ApplyRequest:
    """Everything a run needs, frozen at start (§3.1)."""

    run_id: str
    application_id: str
    job_url: str
    company: str
    role: str
    jd_text: str  # bounded by the caller
    profile_facts: dict[str, str]  # explicit, user-owned grounding facts
    preferences: dict[str, str]  # explicit user preferences (e.g. salary)
    approved_links: tuple[str, ...]  # portfolio/GitHub/LinkedIn the user approved
    artifacts: tuple[ArtifactRef, ...]  # the ONLY uploadable files
    resume_label: str  # "master resume" | "tailored resume" (§3.1 honesty label)
    deadline_s: float = 20 * 60.0  # total run budget (§5.2)
    screenshot_dir: str = ""  # where evidence PNGs are written
    portal_skill: str | None = None  # reserved (§7); no skills ship yet


# ---------------------------------------------------------------------------
# Result (§3.1 / §8.4)
# ---------------------------------------------------------------------------


class ApplyStatus(StrEnum):
    """Terminal states. P1 success is READY_FOR_HUMAN — never 'submitted'."""

    READY_FOR_HUMAN = "ready_for_human"
    BLOCKED = "blocked"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class ApplyPhase(StrEnum):
    """High-level live phase for the companion UI (§8.2)."""

    OPENING = "opening"
    FINDING_FORM = "finding_form"
    FILLING = "filling"
    VERIFYING = "verifying"
    READY_FOR_HUMAN = "ready_for_human"
    BLOCKED = "blocked"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Blocker:
    """One honest obstacle: a field we could not ground, a wall we hit (§6)."""

    kind: str  # ungrounded_field | login_wall | captcha | posting_closed | no_form | error
    detail: str  # redacted, human-readable
    field_label: str | None = None


@dataclass(frozen=True)
class FieldOutcome:
    """What happened to one observed form field (redacted: labels, not values)."""

    label: str
    action: str  # fill | select | check | upload | skipped
    ok: bool
    note: str = ""  # e.g. "read-back mismatch", "no grounded answer"


@dataclass(frozen=True)
class Usage:
    """Exact model spend for the run (§8.2 cost honesty)."""

    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


@dataclass(frozen=True)
class ApplyResult:
    """The durable outcome the app persists (§3.1)."""

    run_id: str
    status: ApplyStatus
    final_url: str
    summary: str  # redacted, human-readable
    page_states: tuple[PageState, ...]  # what the last observation looked like
    fields: tuple[FieldOutcome, ...]
    blockers: tuple[Blocker, ...]
    screenshots: tuple[str, ...]  # paths under ApplyRequest.screenshot_dir
    usage: Usage
    steps: int


# ---------------------------------------------------------------------------
# Events (§9.2) — streamed to the app's runner/SSE hub
# ---------------------------------------------------------------------------


class ApplyEventType(StrEnum):
    PHASE_CHANGED = "apply.phase_changed"
    OBSERVED = "apply.observed"
    ACTION_STARTED = "apply.action_started"
    ACTION_VERIFIED = "apply.action_verified"
    ACTION_FAILED = "apply.action_failed"
    BLOCKER_FOUND = "apply.blocker_found"
    SCREENSHOT_READY = "apply.screenshot_ready"
    READY_FOR_HUMAN = "apply.ready_for_human"
    INTERRUPTED = "apply.interrupted"
    COMPLETED = "apply.completed"


@dataclass(frozen=True)
class ApplyEvent:
    """One redacted progress event. ``data`` holds labels/urls/phase names —
    never raw form values and never a full model prompt (§9.1)."""

    type: ApplyEventType
    data: dict[str, Any] = field(default_factory=dict)


ApplyEventSink = Callable[[ApplyEvent], None]


# ---------------------------------------------------------------------------
# Control (§8.2 cancel / §8.3 interruption)
# ---------------------------------------------------------------------------


class ApplyControl:
    """Cooperative cancellation. The loop polls ``cancelled`` between steps;
    the browser closing is detected separately and maps to INTERRUPTED."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ApplyError(Exception):
    """Package-level failure with an honest, redacted message."""


class StaleElementError(ApplyError):
    """An action referenced an element id from an expired observation (§4.1)."""


class DisallowedActionError(ApplyError):
    """The model asked for something outside the executor's contract (§4.3)."""
