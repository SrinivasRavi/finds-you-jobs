"""The exception router for logged-in LinkedIn runs (section 11, Our Claim 12).

finds-you-jobs-owned (AGPL-3.0-only). A router, not a decision-maker. It maps a
recognized worker error onto the coded handler that already knows how to act on
it, and an UNRECOGNIZED error onto `unknown`, which hard-stops, captures the DOM
and a screenshot so the surprise becomes the next fixture, and asks the model for
exactly one diagnosis. The model returns a label, never an action; the coded
handlers act (Our Claim 12). Safety lives in the handler; accuracy is all the
model is asked for, and it runs only on the unrecognized path.

It imports (never copies) the GPLv3 upstream error TYPES for classification —
through the AGPL facade (`referral_outreach.facade`), never `upstream.*`
directly, so the F-P10 one-way rule holds (`test_upstream_import_boundary`). No
upstream code is carried here, so this file stays AGPL-3.0-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..logging_setup import get_logger

UNKNOWN = "unknown"

# The model's fixed diagnosis vocabulary on the unknown path (section 11.2's four
# classes, plus `unknown` itself). The model picks exactly one; anything outside
# this set clamps to `unknown`, so the vocabulary can never widen — a label,
# never an action (Our Claim 12).
MODEL_DIAGNOSES = ("transient", "interface_drift", "throttle_signal", "account_wall", UNKNOWN)

# How much of the page a captured DOM contributes to the model prompt.
_MAX_DOM_CHARS = 8000
# How long a capture (DOM read / screenshot) may take before it is given up on;
# the capture is best-effort and never blocks the hard stop.
_CAPTURE_TIMEOUT_SECONDS = 5.0

_SYSTEM_PROMPT = (
    "You are the exception agent for a logged-in browser automation. You are given "
    "one unrecognized error and a snapshot of the page. Return exactly one diagnosis "
    "label from this fixed set, and nothing to act on:\n"
    "- transient: a timeout, a blank or half-loaded render, a 404 shell, flaky network.\n"
    "- interface_drift: a selector stopped matching, a control moved, a new modal appeared.\n"
    "- throttle_signal: a 429 or 999, an unusual-activity screen, sudden throttling.\n"
    "- account_wall: an identity checkpoint, a restriction, a forced password change, a captcha.\n"
    "- unknown: none of the above fits.\n"
    "Reply as two lines:\nLABEL: <one label>\nRATIONALE: <one sentence>\n"
    "Never issue an instruction or an action. Diagnose only."
)


@dataclass(frozen=True)
class Diagnosis:
    """One model diagnosis: a label from `MODEL_DIAGNOSES` and its one-line why.
    Carries no action, by construction (Our Claim 12)."""

    label: str
    rationale: str


@dataclass(frozen=True)
class RoutedException:
    """Where the router sent one error. `handled_by` is `coded` for a recognized
    state (a coded handler acts on `diagnosis`) or `model` for the unknown path
    (the model only named it; the action is the hard stop). Carries no action of
    its own — the coded handlers do."""

    diagnosis: str
    handled_by: str
    rationale: str = ""
    dom: str | None = None
    screenshot: bytes | None = None

    @property
    def is_unknown(self) -> bool:
        return self.handled_by == "model"


def classify(exc: BaseException) -> str | None:
    """The coded handler label for a recognized worker error, or None if
    unrecognized. Imports the error TYPES through the AGPL facade (the F-P10 seam)
    lazily, so importing this module never pulls the browser core. No class here
    subclasses another, so isinstance order is irrelevant."""
    from sidecar.packages.referral_outreach import facade

    table = (
        (facade.AuthenticationError, "auth"),
        (facade.ProfileInaccessibleError, "profile_inaccessible"),
        (facade.SkipProfile, "skip_profile"),
        (facade.ReachedConnectionLimit, "connection_limit"),
        (facade.RateLimited, "rate_limited"),
        (facade.BrowserUnresponsiveError, "browser_unresponsive"),
        (facade.CapExceeded, "cap_exceeded"),
    )
    for typ, label in table:
        if isinstance(exc, typ):
            return label
    return None


def route(
    exc: BaseException, *, surface: Any = None, engine: Any = None
) -> RoutedException:
    """Route one worker error. A recognized state names its coded handler and the
    model is never called. An unrecognized state hard-stops: capture the DOM and a
    screenshot, log a dump, and let the model name it (one label from the fixed
    set, never an action)."""
    label = classify(exc)
    if label is not None:
        return RoutedException(diagnosis=label, handled_by="coded", rationale=str(exc))
    dom = _capture_dom(surface)
    screenshot = _capture_screenshot(surface)
    diagnosis = _diagnose(exc, dom=dom, engine=engine)
    _log_dump(exc, diagnosis, dom, screenshot)
    return RoutedException(
        diagnosis=diagnosis.label,
        handled_by="model",
        rationale=diagnosis.rationale,
        dom=dom,
        screenshot=screenshot,
    )


def _capture_dom(surface: Any) -> str | None:
    """Read `document.documentElement.outerHTML` off the surface through the
    isolated world (invisible to the page's own scripts). Best-effort: a failure
    is logged and returns None, never crashing the hard stop."""
    if surface is None:
        return None
    try:
        html = surface.evaluate_isolated(
            "document.documentElement.outerHTML"
        ).result(timeout=_CAPTURE_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 — the capture is evidence, never load-bearing
        get_logger().warning("exception agent: DOM capture failed", exc_info=True)
        return None
    return html if isinstance(html, str) else None


def _capture_screenshot(surface: Any) -> bytes | None:
    """Capture the surface as image bytes (Phase-2 `screenshot()`). Best-effort,
    same as the DOM read."""
    if surface is None:
        return None
    try:
        data = surface.screenshot().result(timeout=_CAPTURE_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 — the capture is evidence, never load-bearing
        get_logger().warning("exception agent: screenshot capture failed", exc_info=True)
        return None
    return bytes(data) if isinstance(data, (bytes, bytearray)) else None


def _diagnose(exc: BaseException, *, dom: str | None, engine: Any) -> Diagnosis:
    """Ask the model for exactly one diagnosis. No engine, or a model failure,
    diagnoses `unknown` — the stop still stands; only the label is missing."""
    if engine is None:
        return Diagnosis(UNKNOWN, "no engine configured for the exception agent")
    user = _build_user_prompt(exc, dom)
    try:
        text, _usage = engine.complete(_SYSTEM_PROMPT, user)
    except Exception as exc_model:  # noqa: BLE001 — a model failure never crashes the stop
        get_logger().warning("exception agent: model call failed", exc_info=True)
        return Diagnosis(UNKNOWN, f"exception-agent model call failed: {exc_model}")
    return _parse_diagnosis(text)


def _build_user_prompt(exc: BaseException, dom: str | None) -> str:
    snippet = (dom or "")[:_MAX_DOM_CHARS]
    return (
        f"ERROR_TYPE: {type(exc).__name__}\n"
        f"ERROR_MESSAGE: {exc}\n"
        f"PAGE_DOM (truncated):\n{snippet}\n"
    )


def _parse_diagnosis(text: Any) -> Diagnosis:
    """Parse the model's `LABEL:`/`RATIONALE:` reply into exactly one diagnosis.
    An out-of-set or unparseable label clamps to `unknown`, so what comes back is
    always one label from `MODEL_DIAGNOSES`."""
    raw = text if isinstance(text, str) else ""
    label = UNKNOWN
    rationale = raw.strip()
    for line in raw.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("label:"):
            candidate = stripped.split(":", 1)[1].strip().lower()
            label = candidate if candidate in MODEL_DIAGNOSES else UNKNOWN
        elif low.startswith("rationale:"):
            rationale = stripped.split(":", 1)[1].strip()
    return Diagnosis(label, rationale)


def _log_dump(
    exc: BaseException, diagnosis: Diagnosis, dom: str | None, screenshot: bytes | None
) -> None:
    """The unknown-path log dump: the error, the diagnosis, and the sizes of the
    captured evidence (never the bytes themselves)."""
    get_logger().warning(
        "exception agent: unrecognized worker error hard-stopped — "
        "error=%s message=%s diagnosis=%s rationale=%s dom_chars=%d screenshot_bytes=%d",
        type(exc).__name__,
        exc,
        diagnosis.label,
        diagnosis.rationale,
        len(dom or ""),
        len(screenshot or b""),
    )
