# voyager_py/scroll_dynamics.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW code written for the finds-you-jobs fork (GPL, lives in the GPL subtree).
# Not derived from OpenOutreach: upstream emits no wheel events anywhere.
"""Reading-scroll: the wheel channel, which we have never filled at all.

Why. Of 1,025 detected agent sessions of Claude driving Chrome via Playwright,
**wheel events were null in all 1,025** — the single feature that was empty in
100% of them ("What Does It Take to Detect an AI Agent?", arXiv:2607.26935; see
`docs/internal/embedded-browser.md` section 17). It is also functionally required:
LinkedIn lazy-loads profile and results content, so a page we never scroll may
be read incomplete.

What we measured first-party (2026-08-06, macOS, Chrome 150 — scripts and raw
numbers in `docs/internal/evidence/2026-08-06-focus-tab-probe/`, `run4.py` /
`run7.py`):

* A **real** wheel notch posted through the OS HID tap produces ONE `wheel`
  event carrying `deltaY 40`, `wheelDeltaY -120`, and Chromium then animates
  the scroll itself: **11 `scroll` events over 166 ms** in a clean ease-in-out.
* A **CDP** `Input.dispatchMouseEvent {type: mouseWheel, deltaY: 120}` produces
  ONE `wheel` and ONE `scroll` — an **instant jump, no cascade at all**. This
  contradicts `embedded-browser.md` section 17.3's "let Chromium own the physics"; on
  macOS Chromium does not animate a CDP wheel, so the cascade has to be
  generated. A 16-step ramp at display cadence does produce 16 scroll events
  over ~750 ms, so the channel is reachable — just not for free.
* CDP pins `wheelDeltaY` at **-120 regardless of `deltaY`**. A real macOS notch
  is `(deltaY 40, wheelDeltaY -120)`; real pixel-mode scrolling is
  `(deltaY d, wheelDeltaY -3d)`. So sending `deltaY: 120` per notch — the
  obvious choice — produces a `(120, -120)` pair **no macOS device emits**,
  while `deltaY: 40` lands exactly on the real notch signature.

So one "notch" here is emitted as the measured cascade: a short ramp of small
wheel events at display cadence, summing to one platform notch.

Everything below sleeps; it must run on a worker thread, never the event loop.
"""

from __future__ import annotations

import logging
import math
import random
import sys
import time

logger = logging.getLogger("voyager_py.scroll")

# Per-notch scroll distance. macOS is FIRST-PARTY MEASURED (run7.py, real HID
# events: deltaY 40 with wheelDeltaY -120). Windows and Linux come from the
# research digest's per-platform table and are NOT measured here — re-measure
# before trusting them on those platforms.
NOTCH_PX = {"darwin": 40.0, "win32": 100.0, "linux": 120.0}
DEFAULT_NOTCH_PX = 100.0

# The measured shape of one real notch's scroll cascade: element scrollTop went
# 0.5, 2.5, 6, 10.5, 16, 22, 28, 33, 36.5, 39, 40 over 166.4 ms. These are the
# per-step increments of that curve, normalised. Bootstrapped from the
# measurement rather than re-derived from a bezier on purpose: section 17.3's rule is
# that a generator whose moments are *too* clean is its own signature.
NOTCH_PROFILE = (0.5, 2.0, 3.5, 4.5, 5.5, 6.0, 6.0, 5.0, 3.5, 2.5, 1.0)
_PROFILE_SUM = sum(NOTCH_PROFILE)
NOTCH_STEP_MS = 166.4 / len(NOTCH_PROFILE)   # ~15.1 ms, i.e. display cadence

# --- burst / pause structure ------------------------------------------------
# NOT MEASURED. The digest is explicit that no published data exists for
# desktop wheel burst/pause structure, and we did not instrument a human here
# (that needs a consenting subject, not a probe page). These are declared
# defaults, right-skewed for the same reason every other human interval is, and
# they are parameters rather than literals so a later measurement can replace
# them without touching the call sites.
BURST_NOTCHES = (2, 5)          # notches in one flick of the wheel
INTER_NOTCH_MS = (55.0, 145.0)  # gap between notches inside a burst
READ_PAUSE_MU = 7.0             # log-normal: median ~1.1 s
READ_PAUSE_SIGMA = 0.75
READ_PAUSE_CAP_MS = 9000.0
PROFILE_READ_NOTCHES = (6, 14)  # how far a person scrolls down one profile
RESULTS_READ_NOTCHES = (4, 10)  # ...and one page of search results


def notch_px(platform: str | None = None) -> float:
    return NOTCH_PX.get(platform or sys.platform, DEFAULT_NOTCH_PX)


def notch_steps(distance_px: float | None = None,
                platform: str | None = None) -> list[float]:
    """One notch, expanded into the per-step wheel deltas Chromium would have
    animated for us if this were a real device."""
    total = notch_px(platform) if distance_px is None else distance_px
    return [round(total * frac / _PROFILE_SUM, 2) for frac in NOTCH_PROFILE]


def read_pause_ms(rng: random.Random | None = None) -> float:
    """One pause between bursts — the part where a person is actually reading."""
    rng = rng or random
    return min(math.exp(rng.gauss(READ_PAUSE_MU, READ_PAUSE_SIGMA)), READ_PAUSE_CAP_MS)


def _wheel_point(page) -> tuple[float, float]:
    """Where the pointer sits while scrolling. Read from the viewport when
    Playwright knows it, otherwise a plain interior point — deliberately NOT a
    `page.evaluate`, which runs in the main world on the LinkedIn origin."""
    size = getattr(page, "viewport_size", None)
    if isinstance(size, dict) and size.get("width") and size.get("height"):
        return size["width"] * 0.5, size["height"] * 0.55
    return 640.0, 400.0


def scroll_notch(page, *, direction: int = 1, rng=None, sleep=time.sleep,
                 clock=time.monotonic, platform: str | None = None) -> None:
    """Emit ONE wheel notch as its measured multi-event cascade.

    Steps are scheduled against the clock, not slept outright: `mouse.wheel` is
    an awaited CDP round-trip (~18 ms measured), so sleeping the 15 ms step
    naively produced a 33 ms cadence and a 317 ms cascade where the real device
    delivers 166 ms.
    """
    rng = rng or random
    origin = clock()
    target = 0.0
    for i, step in enumerate(notch_steps(platform=platform)):
        page.mouse.wheel(0, step * direction)
        if i + 1 == len(NOTCH_PROFILE):
            break
        target += max(NOTCH_STEP_MS + rng.uniform(-2.5, 2.5), 1.0)
        remaining = target - (clock() - origin) * 1000.0
        if remaining > 0:
            sleep(remaining / 1000.0)


def reading_scroll(page, *, notches: int, direction: int = 1, rng=None,
                   sleep=time.sleep, clock=time.monotonic,
                   platform: str | None = None) -> int:
    """Scroll `notches` notches in bursts, pausing to "read" between them.

    Returns the number of notches actually emitted. Never raises: a page that
    cannot take a wheel event is not a reason to abandon the LinkedIn action
    that asked for the scroll.
    """
    rng = rng or random
    try:
        page.mouse.move(*_wheel_point(page))
    except Exception:  # noqa: BLE001 — pointer placement is best-effort
        pass
    done = 0
    try:
        while done < notches:
            burst = min(rng.randint(*BURST_NOTCHES), notches - done)
            for i in range(burst):
                scroll_notch(page, direction=direction, rng=rng, sleep=sleep,
                             clock=clock, platform=platform)
                done += 1
                if i + 1 < burst:
                    sleep(rng.uniform(*INTER_NOTCH_MS) / 1000.0)
            if done < notches:
                sleep(read_pause_ms(rng) / 1000.0)
    except Exception as e:  # noqa: BLE001 — never fail an action over a scroll
        logger.debug("reading_scroll stopped after %d notches: %s", done, e)
    return done


def read_profile(page, rng=None, sleep=time.sleep, clock=time.monotonic) -> int:
    """The scroll a person does on arriving at a profile, before acting on it.
    Also what makes LinkedIn's lazy-loaded profile sections actually render."""
    rng = rng or random
    return reading_scroll(page, notches=rng.randint(*PROFILE_READ_NOTCHES),
                          rng=rng, sleep=sleep, clock=clock)


def read_results(page, rng=None, sleep=time.sleep, clock=time.monotonic) -> int:
    """The scroll a person does while walking a page of search results."""
    rng = rng or random
    return reading_scroll(page, notches=rng.randint(*RESULTS_READ_NOTCHES),
                          rng=rng, sleep=sleep, clock=clock)
