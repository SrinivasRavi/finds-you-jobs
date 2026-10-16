"""The exception router for logged-in LinkedIn runs (section 11, Our Claim 12).

A router, not a decision-maker: a recognized worker error maps to its coded handler.
An unrecognized one hard-stops, captures the page (URL, DOM, screenshot), and gets 1
diagnosis label: from fixed rules over that evidence when one matches, at no cost,
else from the model. `explain` turns the label into words the user reads. Imports
the GPLv3 error types through the AGPL facade, never `upstream.*` (F-P10)."""

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

# What the user reads for each label: what happened, and what to do about it.
EXPLANATIONS: dict[str, str] = {
    "transient": "LinkedIn didn't load in time or the connection dropped. Trying again "
    "later should work.",
    "interface_drift": "LinkedIn's page changed, so a button or field wasn't where the "
    "app expected it.",
    "throttle_signal": "LinkedIn is limiting this account's activity right now. Wait "
    "before sending more.",
    "account_wall": "LinkedIn wants you to verify the account (a sign-in, checkpoint or "
    "captcha). Open LinkedIn and clear it before anything else runs.",
    UNKNOWN: "The app couldn't tell what LinkedIn showed.",
}

# Fixed rules over the evidence, checked before any model call: (label, signals),
# first match wins. Each signal is a lowercase substring of the error chain, the
# page URL, or the captured DOM.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("account_wall", ("/checkpoint", "/authwall", "/uas/login", "captcha",
                      "security verification", "let's do a quick security check")),
    ("throttle_signal", ("http 999", "http 429", "too many requests",
                         "unusual activity", "you've reached the weekly")),
    ("interface_drift", ("no connect affordance", "could not be confirmed sent",
                         "waiting for locator", "selector")),
    ("transient", ("timeouterror", "timeout", "net::err_", "target page, context or "
                   "browser has been closed", "browseractiontimeout",
                   "browser surface", "connection reset")),
)

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
        """An unrecognized error, labelled by the rules or the model."""
        return self.handled_by != "coded"

    @property
    def explanation(self) -> str:
        """The label in words, for an unrecognized error; "" for a coded one, whose
        own message already says what happened."""
        return explain(self.diagnosis) if self.is_unknown else ""


def explain(label: str) -> str:
    return EXPLANATIONS.get(label, EXPLANATIONS[UNKNOWN])


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


def _chain(exc: BaseException) -> list[BaseException]:
    """The exception and everything it was raised `from`, outermost first."""
    links: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        links.append(cur)
        cur = cur.__cause__
    return links


def route_cause(
    exc: BaseException, *, surface: Any = None, engine: Any = None
) -> RoutedException:
    """Route the worker error behind a module-typed wrapper. The networker driver
    re-raises `NetworkerError(...) from e`, so the recognizable state is the
    `__cause__`; routing the wrapper itself would send every recognized state
    down the unknown path and call the model on errors we already understand."""
    links = _chain(exc)
    for link in links:
        if classify(link) is not None:
            return route(link, surface=surface, engine=engine)
    return route(links[-1], surface=surface, engine=engine)


def route(
    exc: BaseException, *, surface: Any = None, engine: Any = None
) -> RoutedException:
    """Route one worker error. A recognized state names its coded handler and the
    model is never called. An unrecognized state hard-stops: capture the page, log a
    dump, and name it, by rule when the evidence decides, else by the model (one
    label from the fixed set, never an action)."""
    label = classify(exc)
    if label is not None:
        return RoutedException(diagnosis=label, handled_by="coded", rationale=str(exc))
    url = _capture_url(surface)
    dom = _capture_dom(surface)
    screenshot = _capture_screenshot(surface)
    ruled = _rule_diagnosis(exc, url=url, dom=dom)
    diagnosis = ruled or _diagnose(exc, url=url, dom=dom, engine=engine)
    _log_dump(exc, diagnosis, dom, screenshot)
    return RoutedException(
        diagnosis=diagnosis.label,
        handled_by="rules" if ruled else "model",
        rationale=diagnosis.rationale,
        dom=dom,
        screenshot=screenshot,
    )


def _rule_diagnosis(exc: BaseException, *, url: str, dom: str | None) -> Diagnosis | None:
    """The first rule whose signal appears in the evidence, or None."""
    chain = " ".join(f"{type(link).__name__} {link}" for link in _chain(exc))
    haystacks = ((chain.lower(), "the error"), (url.lower(), "the page URL"),
                 ((dom or "")[:_MAX_DOM_CHARS].lower(), "the page"))
    for label, signals in _RULES:
        for text, where in haystacks:
            hit = next((sig for sig in signals if sig in text), None)
            if hit is not None:
                return Diagnosis(label, f"{where} contains {hit!r}")
    return None


def _capture_url(surface: Any) -> str:
    """The surface's committed URL, read off the broker's own copy (no lane trip)."""
    url = getattr(surface, "page_url", "") if surface is not None else ""
    return url if isinstance(url, str) else ""


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


def _diagnose(exc: BaseException, *, url: str, dom: str | None, engine: Any) -> Diagnosis:
    """Ask the model for exactly one diagnosis. No engine, or a model failure,
    diagnoses `unknown` — the stop still stands; only the label is missing."""
    if engine is None:
        return Diagnosis(UNKNOWN, "no engine configured for the exception agent")
    user = _build_user_prompt(exc, url, dom)
    try:
        text, _usage = engine.complete(_SYSTEM_PROMPT, user)
    except Exception as exc_model:  # noqa: BLE001 — a model failure never crashes the stop
        get_logger().warning("exception agent: model call failed", exc_info=True)
        return Diagnosis(UNKNOWN, f"exception-agent model call failed: {exc_model}")
    return _parse_diagnosis(text)


def _build_user_prompt(exc: BaseException, url: str, dom: str | None) -> str:
    snippet = (dom or "")[:_MAX_DOM_CHARS]
    return (
        f"ERROR_TYPE: {type(exc).__name__}\n"
        f"ERROR_MESSAGE: {exc}\n"
        f"PAGE_URL: {url}\n"
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
