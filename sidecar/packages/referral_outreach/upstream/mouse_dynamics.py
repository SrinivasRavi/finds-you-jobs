# voyager_py/mouse_dynamics.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW code written for the finds-you-jobs fork (GPL, lives in the GPL subtree).
# Not derived from OpenOutreach: upstream clicks with no pointer motion at all.
"""Pointer motion before a click, and a pointerdown that carries real pressure.

Two defects this closes, both first-party measured:

1. **Teleport.** Raw CDP clicks emit ZERO `mousemove` before the press
   (`embedded-browser.md` §15.3: "teleport — moves before click: 0"). In the
   2026 agent-detection work `teleport_click_ratio` is one of the two features
   that reach 100% agent recall, so a click with no approach is the single
   most-cited mouse tell. Generating a trail took moves-before from 0 to 25 in
   our own measurement.
2. **`pressure: 0` on pointerdown.** A real mouse leaves `force` as NaN and
   Blink maps that to **0.5**; CDP passes `force.value_or(0)`, so we emit 0.
   Measured directly in Phase 0 (`run4.py`, item 0.2): default CDP pointerdown
   → `pressure 0`; passing `force: 0.5` → `pressure 0.5`, matching a real mouse.
   Playwright's `mouse.down()` has no `force` parameter, so the press has to go
   through a raw CDP session.

Trajectory parameters come from the research digest's Mouse table. What is
deliberately NOT done, per §17.3:

* no clean 10 Hz tremor sinusoid — the per-sample noise is low-amplitude and
  broadband;
* no exact Fitts fit — movement time carries ~22% log-normal spread, so a
  regression over our trials cannot come back r² = 1.0, which is itself a tell;
* no chasing `getCoalescedEvents()` richness. Phase 0 showed CDP CAN coalesce
  when commands are pipelined rather than awaited (10 moves → 1 event carrying
  10 samples), but Playwright's sync API awaits every command, so through this
  driver each move arrives as its own event with one sample. Emitting at
  display cadence is the part that matters — page JS sees mousemove rAF-aligned
  regardless.

Everything here sleeps; worker thread only, never the event loop.
"""

from __future__ import annotations

import logging
import math
import random
import time
from weakref import WeakKeyDictionary

logger = logging.getLogger("voyager_py.mouse")

# --- Fitts / movement time --------------------------------------------------
# Shannon formulation, ID = log2(D/W + 1), W = min(w, h) for a 2D target.
# a/b chosen so a 400 px reach to a 100x30 button lands at ~850 ms, the centre
# of the measured 750-1050 ms band; b = 195 ms/bit is inside the 3.7-4.9 bits/s
# throughput range every ISO-conforming mouse study agrees on.
FITTS_A_MS = 100.0
FITTS_B_MS = 195.0
MT_SIGMA = 0.22          # log-normal spread, measured 20-25%
MT_MIN_MS, MT_MAX_MS = 120.0, 2400.0

# Velocity profile within one submovement: peak at 30% of that submovement's
# duration (NOT 50%, and not the 35-45% an earlier brief assumed), with the long
# deceleration tail the literature reports. Mode of Beta(a, b) is
# (a-1)/(a+b-2), so b = 1/0.3 for a = 2.
VEL_ALPHA, VEL_BETA = 2.0, 1 / 0.3

# Submovements: the primary covers ~59% of the distance and UNDERSHOOTS;
# ~4.3 submovements total for an adult.
PRIMARY_FRACTION = (0.52, 0.66)
CORRECTION_FRACTION = (0.45, 0.80)
MAX_SUBMOVEMENTS = 5
INTER_SUBMOVEMENT_MS = (30.0, 80.0)
OVERSHOOT_PROBABILITY = 0.09      # held under 10%
TARGET_RE_ENTRY_PROBABILITY = 0.07

# Path curvature: mean |perpendicular deviation| 2.9% of D, signed bow 0.6%.
BOW_FRACTION = (0.06, 0.12)
JITTER_PX = 0.45                  # broadband, NOT a tremor sinusoid

# Sampling: page JS sees mousemove rAF-aligned to the display (~60/s), so
# emitting at a 125 Hz or 1000 Hz device rate is itself anomalous.
FRAME_MS = 16.7
FRAME_JITTER_MS = 2.4

# Real-mouse pointerdown pressure (Blink's NaN sentinel).
POINTER_FORCE = 0.5

# Click hold (press→release). The digest is explicit that NO population
# statistic for this appears to exist anywhere, so this is a declared default,
# not a measurement.
CLICK_HOLD_MU = 4.45              # log-normal, median ~86 ms
CLICK_HOLD_SIGMA = 0.30

# Where the pointer is assumed to be the first time we touch a page.
_RESTING_POINT = (12.0, 96.0)
_positions: WeakKeyDictionary = WeakKeyDictionary()
_cdp_sessions: WeakKeyDictionary = WeakKeyDictionary()


def pointer_position(page) -> tuple[float, float]:
    return _positions.get(page, _RESTING_POINT)


def _set_pointer_position(page, x: float, y: float) -> None:
    try:
        _positions[page] = (x, y)
    except TypeError:  # pragma: no cover — a page that cannot be weak-referenced
        pass


def movement_time_ms(distance: float, target_w: float, target_h: float,
                     rng: random.Random | None = None) -> float:
    """Fitts' law with real spread, so the fit can never come out perfect."""
    rng = rng or random
    width = max(min(target_w, target_h), 1.0)
    index_of_difficulty = math.log2(max(distance, 1.0) / width + 1.0)
    nominal = FITTS_A_MS + FITTS_B_MS * index_of_difficulty
    return min(max(nominal * math.exp(rng.gauss(0.0, MT_SIGMA)), MT_MIN_MS), MT_MAX_MS)


def _velocity_cdf(tau: float) -> float:
    """Fraction of a submovement's distance covered by time fraction `tau`.

    Integral of a Beta(VEL_ALPHA, VEL_BETA) velocity profile, evaluated by a
    small fixed quadrature — enough resolution for 60 Hz sampling and free of a
    scipy dependency.
    """
    steps = 64
    total = 0.0
    partial = 0.0
    for i in range(steps):
        t = (i + 0.5) / steps
        v = t ** (VEL_ALPHA - 1) * (1 - t) ** (VEL_BETA - 1)
        total += v
        if t <= tau:
            partial += v
    return partial / total if total else tau


def _submovement_targets(start, end, rng) -> list[tuple[float, float]]:
    """Where each submovement lands. The primary undershoots; corrections
    converge; occasionally one overshoots and re-enters the target."""
    sx, sy = start
    ex, ey = end
    points = []
    cx, cy = sx, sy
    frac = rng.uniform(*PRIMARY_FRACTION)
    for i in range(MAX_SUBMOVEMENTS):
        if i and rng.random() < OVERSHOOT_PROBABILITY:
            frac = rng.uniform(1.02, 1.10)          # a rare, small overshoot
        cx, cy = cx + (ex - cx) * frac, cy + (ey - cy) * frac
        points.append((cx, cy))
        if math.hypot(ex - cx, ey - cy) < 1.5:
            break
        frac = rng.uniform(*CORRECTION_FRACTION)
    points[-1] = (ex, ey)
    if rng.random() < TARGET_RE_ENTRY_PROBABILITY:
        # Leave the target and come back — measured at 0.07 per trial.
        points.insert(-1, (ex + rng.uniform(-9, 9), ey + rng.uniform(-9, 9)))
    return points


def _bowed(a, b, tau, bow, rng) -> tuple[float, float]:
    """A point at time-fraction `tau` along a bowed path from a to b, plus
    low-amplitude broadband noise (never a fixed-frequency tremor)."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length          # unit normal
    arch = math.sin(math.pi * tau) * bow
    return (ax + dx * tau + nx * arch + rng.gauss(0.0, JITTER_PX),
            ay + dy * tau + ny * arch + rng.gauss(0.0, JITTER_PX))


def _leg_durations(total_ms: float, spans: list[float],
                   rng: random.Random) -> list[tuple[float, float]]:
    """Split the movement time into (motion, pause) per submovement.

    Motion time follows DISTANCE, sub-linearly (d^0.85, so short corrections are
    relatively slower, as visually guided corrections are). Two earlier
    allocations were wrong in ways the trajectory measurements caught:

    * pausing BETWEEN submovements on top of the movement time pushed a 400 px
      reach to 1060 ms against a measured 750-1050 ms band. The pauses are part
      of the movement time.
    * giving corrections a fixed share of the budget made them FASTER than the
      primary submovement — peak velocity landed at 2400 px/s in a correction
      leg, against a measured ~1500 px/s in the primary. Velocity has to be
      comparable across legs, which only distance-proportional time gives.
    """
    pauses = [rng.uniform(*INTER_SUBMOVEMENT_MS) for _ in spans]
    # Pauses are capped as a share of the movement, never an absolute cost: a
    # fast trial (movement time is log-normal, 10th percentile 0.75x) would
    # otherwise pay the same ~220 ms out of a much smaller budget.
    if sum(pauses) > total_ms * 0.25:
        squeeze = total_ms * 0.25 / sum(pauses)
        pauses = [p * squeeze for p in pauses]
    motion = total_ms - sum(pauses)
    weights = [max(s, 1.0) ** 0.85 for s in spans]
    scale = motion / sum(weights)
    return list(zip([w * scale for w in weights], pauses, strict=True))


def trail(start, end, *, target_w=20.0, target_h=20.0,
          rng: random.Random | None = None) -> list[tuple[float, float, float]]:
    """The full approach path as (t_ms, x, y), sampled at display cadence.

    Pure — no page, no sleeping — so the trajectory can be asserted on directly.
    """
    rng = rng or random
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    if distance < 1.0:
        return []
    total_ms = movement_time_ms(distance, target_w, target_h, rng)
    targets = _submovement_targets(start, end, rng)
    spans = []
    prev = start
    for point in targets:
        spans.append(math.hypot(point[0] - prev[0], point[1] - prev[1]))
        prev = point
    durations = _leg_durations(total_ms, spans, rng)

    out: list[tuple[float, float, float]] = []
    t = 0.0
    prev = start
    for point, (leg_ms, pause) in zip(targets, durations, strict=True):
        span = math.hypot(point[0] - prev[0], point[1] - prev[1])
        bow = span * rng.uniform(*BOW_FRACTION) * rng.choice((-1.0, 1.0))
        frames = max(int(round(leg_ms / FRAME_MS)), 1)
        for i in range(1, frames + 1):
            tau = _velocity_cdf(i / frames)
            t += max(FRAME_MS + rng.gauss(0.0, FRAME_JITTER_MS), 4.0)
            x, y = _bowed(prev, point, tau, bow, rng)
            out.append((t, x, y))
        out[-1] = (out[-1][0], point[0], point[1])
        prev = point
        t += pause
    return out


def click_hold_ms(rng: random.Random | None = None) -> float:
    rng = rng or random
    return min(max(math.exp(rng.gauss(CLICK_HOLD_MU, CLICK_HOLD_SIGMA)), 30.0), 400.0)


def target_point(box: dict, rng: random.Random | None = None) -> tuple[float, float]:
    """Where inside the element the pointer actually lands. People do not click
    the exact geometric centre every time."""
    rng = rng or random
    return (box["x"] + box["width"] * min(max(rng.gauss(0.5, 0.16), 0.12), 0.88),
            box["y"] + box["height"] * min(max(rng.gauss(0.5, 0.16), 0.15), 0.85))


def approach(page, x: float, y: float, *, target_w=20.0, target_h=20.0,
             rng=None, sleep=time.sleep, clock=time.monotonic) -> int:
    """Move the pointer to (x, y) along a human path. Returns moves emitted.

    Best-effort: a page that will not take a move is never a reason to abandon
    the action that wanted to click.
    """
    rng = rng or random
    start = pointer_position(page)
    path = trail(start, (x, y), target_w=target_w, target_h=target_h, rng=rng)
    origin = clock()
    emitted = 0
    try:
        for t_ms, px, py in path:
            remaining = t_ms - (clock() - origin) * 1000.0
            if remaining > 0:
                sleep(remaining / 1000.0)
            page.mouse.move(px, py)
            emitted += 1
        _set_pointer_position(page, x, y)
    except Exception as e:  # noqa: BLE001 — motion is realism, not correctness
        logger.debug("approach stopped after %d moves: %s", emitted, e)
    return emitted


def _cdp(page):
    session = _cdp_sessions.get(page)
    if session is None:
        session = page.context.new_cdp_session(page)
        _cdp_sessions[page] = session
    return session


def press_and_release(page, x: float, y: float, *, rng=None, sleep=time.sleep) -> None:
    """A click whose pointerdown carries a real mouse's pressure.

    Goes through a raw CDP session because Playwright's `mouse.down()` has no
    `force` parameter, and without it Blink reports `pressure: 0` — which no
    physical mouse ever produces.
    """
    rng = rng or random
    cdp = _cdp(page)
    base = {"x": x, "y": y, "button": "left", "clickCount": 1}
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mousePressed", "buttons": 1, "force": POINTER_FORCE, **base})
    sleep(click_hold_ms(rng) / 1000.0)
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mouseReleased", "buttons": 0, "force": POINTER_FORCE, **base})


def human_click(locator, *, fallback=None, rng=None, sleep=time.sleep,
                clock=time.monotonic, timeout_ms: int = 5000) -> bool:
    """Approach the element, then click it with a real pressure value.

    Returns True when the human path was used, False when it degraded to
    `fallback()` — which is the caller's own existing click, unchanged, so a
    layout this cannot measure behaves exactly as it did before this landed.
    """
    rng = rng or random
    page = None
    try:
        page = locator.page
        locator.scroll_into_view_if_needed(timeout=timeout_ms)
        box = locator.bounding_box(timeout=timeout_ms)
        if not box or box["width"] <= 0 or box["height"] <= 0:
            raise ValueError("no bounding box")
        x, y = target_point(box, rng)
        approach(page, x, y, target_w=box["width"], target_h=box["height"],
                 rng=rng, sleep=sleep, clock=clock)
        press_and_release(page, x, y, rng=rng, sleep=sleep)
        return True
    except Exception as e:  # noqa: BLE001 — degrade to the caller's own click
        logger.debug("human_click degraded to the plain click: %s", e)
        if fallback is not None:
            fallback()
        return False
