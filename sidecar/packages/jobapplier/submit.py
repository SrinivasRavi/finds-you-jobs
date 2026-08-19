# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""App-side submit executor.

Deliberately OUTSIDE the model's tool vocabulary: the agent loop still cannot
submit — ``actions.py`` has no submit tool and the executor would refuse one.
The app calls this exactly once, after the user's Submit click in the review
modal (the ruled P1 operating model: the user's click drives the real submit),
on the same live page the loop left at ``ready_for_human``.

Finding the control is deterministic: a type=submit button or submit-worded
control inside the filled form, with a deny-list for the lookalikes the sweep
measured ("Apply Later" outranked the real button on a text-plus-area scorer;
applier.md, Our Finding 11.1). Confirmation is then classified the same way
the loop classifies everything else; the caller keeps polling afterwards, so
a slow confirmation is not lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .classifier import classify
from .observe import ObservedElement, observe
from .types import PageState
from .upstream.constants import SKYVERN_ID_ATTR

_SUBMIT_TEXT = re.compile(
    r"\b(submit(\s+(my|your|this))?(\s+application)?|send\s+application|"
    r"apply\s+now|apply)\b",
    re.IGNORECASE,
)
_SUBMIT_DENY = re.compile(
    r"\b(apply\s+later|save|cancel|back|draft|clear|reset|delete|withdraw|"
    r"preview|later)\b",
    re.IGNORECASE,
)

_CLICK_TIMEOUT_MS = 10_000
_CONFIRM_POLL_S = 2.0
_CONFIRM_WAIT_S = 20.0


@dataclass(frozen=True)
class SubmitOutcome:
    """What the one submit click actually did, honestly."""

    clicked: bool
    confirmed: bool
    note: str
    final_url: str


def _score(element: ObservedElement) -> int | None:
    """Rank a candidate submit control; None means not a candidate."""
    tag = element.tag.lower()
    if tag not in {"button", "input", "a"}:
        return None
    text = f"{element.label} {element.text}".strip()
    if _SUBMIT_DENY.search(text):
        return None
    etype = (element.attributes.get("type") or "").lower()
    is_submit_typed = etype == "submit" and tag in {"button", "input"}
    worded = bool(_SUBMIT_TEXT.search(text))
    if is_submit_typed and worded:
        return 4
    if is_submit_typed:
        return 3
    if worded and tag == "button":
        return 2
    if worded:
        return 1
    return None


async def submit_application(page: Page) -> SubmitOutcome:
    """Click the form's real submit control once and watch for confirmation."""
    try:
        obs = await observe(page)
    except PlaywrightError:
        return SubmitOutcome(False, False, "page was gone before submit", "")

    best: tuple[int, int, ObservedElement] | None = None
    for position, element in enumerate(obs.elements):
        score = _score(element)
        if score is None:
            continue
        # Ties go to the LAST candidate in document order: the real submit
        # sits at the end of the form, decoys and nav controls above it.
        if best is None or (score, position) >= (best[0], best[1]):
            best = (score, position, element)
    if best is None:
        return SubmitOutcome(False, False, "no submit control found", page.url)

    element = best[2]
    selector = f'[{SKYVERN_ID_ATTR}="{element.unique_id}"]'
    locator = page.locator(selector).first
    if not await locator.count():
        for frame in page.frames:
            candidate = frame.locator(selector).first
            if await candidate.count():
                locator = candidate
                break
        else:
            return SubmitOutcome(
                False, False, "submit control vanished before the click", page.url
            )
    try:
        await locator.click(timeout=_CLICK_TIMEOUT_MS)
    except PlaywrightError as exc:
        reason = str(exc).splitlines()[0][:200]
        return SubmitOutcome(False, False, f"submit click failed: {reason}", page.url)

    label = element.label or element.text.strip() or element.tag
    note = f"clicked {label!r}"
    waited = 0.0
    while waited < _CONFIRM_WAIT_S:
        await page.wait_for_timeout(_CONFIRM_POLL_S * 1000)
        waited += _CONFIRM_POLL_S
        try:
            states = classify(await observe(page))
        except PlaywrightError:
            return SubmitOutcome(True, False, f"{note}; page closed after submit", "")
        if PageState.CONFIRMATION in states:
            return SubmitOutcome(
                True, True, f"{note}; confirmation detected", page.url
            )
        if PageState.VALIDATION_ERROR in states:
            return SubmitOutcome(
                True,
                False,
                f"{note}; the form raised validation errors instead of confirming",
                page.url,
            )
    return SubmitOutcome(
        True, False, f"{note}; no confirmation within {int(_CONFIRM_WAIT_S)}s", page.url
    )
