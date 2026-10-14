# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""App-side submit executor, outside the model's tool vocabulary.

Called after the user's Submit click, on the page the loop left at
`ready_for_human`. The control is found deterministically (a submit button or
submit-worded control in the form, with a deny-list for lookalikes like "Apply
Later"; applier.md Our Finding 11.1). The click is the point of no return: past
it the outcome is read off the page, and silence is `unconfirmed`, never "not
sent" (audit-2026-10-14.md section 8.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

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
# A Submit after an unconfirmed one reads this long before it decides to click.
_SETTLE_WAIT_S = 30.0


@dataclass(frozen=True)
class SubmitOutcome:
    """What the one submit click actually did, honestly."""

    clicked: bool
    confirmed: bool
    note: str
    final_url: str
    # Clicked, and the page shows neither a confirmation nor an error.
    unconfirmed: bool = False


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


async def _click(locator: Any) -> None:
    await locator.click(timeout=_CLICK_TIMEOUT_MS)


def _best_control(obs: Any) -> tuple[int, ObservedElement] | None:
    best: tuple[int, int, ObservedElement] | None = None
    for position, element in enumerate(obs.elements):
        score = _score(element)
        if score is None:
            continue
        # Ties go to the LAST candidate in document order: the real submit
        # sits at the end of the form, decoys and nav controls above it.
        if best is None or (score, position) >= (best[0], best[1]):
            best = (score, position, element)
    return None if best is None else (best[0], best[2])


async def _read_outcome(page: Page, wait_s: float) -> tuple[str, str]:
    """Poll the page up to `wait_s`: ("confirmed" | "error" | "closed" | "silent", url)."""
    waited = 0.0
    while waited < wait_s:
        await page.wait_for_timeout(_CONFIRM_POLL_S * 1000)
        waited += _CONFIRM_POLL_S
        try:
            states = classify(await observe(page))
        except PlaywrightError:
            return "closed", ""
        if PageState.CONFIRMATION in states:
            return "confirmed", page.url
        if PageState.VALIDATION_ERROR in states:
            return "error", page.url
    return "silent", page.url


async def submit_application(page: Page, *, after_doubt: bool = False) -> SubmitOutcome:
    """Click the form's real submit control once and read what the page says.

    `after_doubt` is a Submit on a run whose last click ended unconfirmed: it
    reads first, and clicks only if the form's own submit control is still
    there with nothing said for the whole settle wait."""
    if after_doubt:
        verdict, url = await _read_outcome(page, _SETTLE_WAIT_S)
        if verdict == "confirmed":
            return SubmitOutcome(False, True, "the earlier submit was confirmed", url)
        if verdict == "closed":
            return SubmitOutcome(False, False, "page closed after submit", "", unconfirmed=True)
    try:
        obs = await observe(page)
    except PlaywrightError:
        return SubmitOutcome(False, False, "page was gone before submit", "")

    found = _best_control(obs)
    if after_doubt and (found is None or found[0] < 3):
        # The form moved on and nothing says sent or not: another click
        # would hit whatever button the new page has.
        return SubmitOutcome(
            False, False, "the page changed after the earlier submit", page.url,
            unconfirmed=True,
        )
    if found is None:
        return SubmitOutcome(False, False, "no submit control found", page.url)

    element = found[1]
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
    label = element.label or element.text.strip() or element.tag
    note = f"clicked {label!r}"
    try:
        await _click(locator)
    except PlaywrightError as exc:
        # The click may have reached the page before it raised, so it counts
        # as past the point of no return and the page decides.
        note = f"{note} (the click raised: {str(exc).splitlines()[0][:160]})"

    verdict, url = await _read_outcome(page, _CONFIRM_WAIT_S)
    if verdict == "confirmed":
        return SubmitOutcome(True, True, f"{note}; confirmation detected", url)
    if verdict == "error":
        return SubmitOutcome(
            True, False, f"{note}; the form raised validation errors instead of confirming", url
        )
    if verdict == "closed":
        return SubmitOutcome(True, False, f"{note}; page closed after submit", "", unconfirmed=True)
    return SubmitOutcome(
        True, False, f"{note}; no confirmation within {int(_CONFIRM_WAIT_S)}s", url,
        unconfirmed=True,
    )
