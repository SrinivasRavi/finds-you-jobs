# voyager_py/tests/test_scroll_dynamics.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: S311 — seeded PRNGs make these distribution assertions reproducible.
"""Reading-scroll — the wheel channel we never filled.

Wheel events were null in 100% of 1,025 detected agent sessions, so "we emit
any wheel at all" is the headline assertion; the rest checks that what we emit
has the SHAPE we measured off a real device rather than the single instant
jump a naive CDP wheel produces.

Measured references (`docs/internal/evidence/2026-08-06-focus-tab-probe/`,
run7.py, real HID events on macOS): one notch = deltaY 40 total, delivered as
an 11-event ease-in-out over ~166 ms. A single CDP wheel of 120 gives one
event and an instant jump.
"""

from __future__ import annotations

import random
import statistics

from sidecar.packages.referral_outreach.upstream import scroll_dynamics as sd


class FakeMouse:
    def __init__(self, clock, explode=False):
        self.clock = clock
        self.explode = explode
        self.wheels: list[tuple[float, float, float]] = []   # (t_ms, dx, dy)
        self.moves: list[tuple[float, float]] = []

    def wheel(self, dx, dy):
        if self.explode:
            raise RuntimeError("page went away mid-scroll")
        self.wheels.append((self.clock(), dx, dy))

    def move(self, x, y):
        self.moves.append((x, y))


class FakePage:
    def __init__(self, clock, viewport=None, explode=False):
        self.mouse = FakeMouse(clock, explode)
        self.viewport_size = viewport


class Clock:
    """Monotonic, in SECONDS, advancing only when slept on — so the cadence the
    generator planned is the cadence the log shows."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def ms(self):
        return self.t * 1000.0

    def sleep(self, seconds):
        assert seconds >= 0
        self.t += seconds


def _run(fn, **kw):
    clock = Clock()
    page = FakePage(clock.ms, viewport=kw.pop("viewport", {"width": 1280, "height": 800}))
    n = fn(page, rng=random.Random(20260806), sleep=clock.sleep, clock=clock, **kw)
    return page, clock, n


# --- the notch cascade ------------------------------------------------------
def test_one_notch_is_a_multi_event_cascade_not_a_single_jump():
    steps = sd.notch_steps(platform="darwin")
    assert len(steps) == 11                       # measured: 11 scroll events
    assert abs(sum(steps) - 40.0) < 0.2           # measured: deltaY 40 per notch
    assert all(s > 0 for s in steps)


def test_notch_profile_is_ease_in_out_not_constant_velocity():
    steps = sd.notch_steps(platform="darwin")
    peak = steps.index(max(steps))
    assert 0 < peak < len(steps) - 1              # accelerates then decelerates
    assert steps[0] < max(steps) and steps[-1] < max(steps)
    assert statistics.pstdev(steps) > 1.0         # a constant ramp would be 0


def test_per_platform_notch_distance():
    # macOS is first-party measured; the others come from the digest's table.
    assert sd.notch_px("darwin") == 40.0
    assert sd.notch_px("win32") == 100.0
    assert sd.notch_px("linux") == 120.0
    assert sd.notch_px("plan9") == sd.DEFAULT_NOTCH_PX


def test_notch_step_cadence_is_display_rate():
    assert 12.0 <= sd.NOTCH_STEP_MS <= 20.0       # ~60 Hz


# --- the burst structure ----------------------------------------------------
def test_reading_scroll_emits_wheel_events_at_all():
    page, _clock, n = _run(sd.reading_scroll, notches=6)
    assert n == 6
    assert len(page.mouse.wheels) == 6 * len(sd.NOTCH_PROFILE)
    assert all(dy > 0 for _t, _dx, dy in page.mouse.wheels)
    assert page.mouse.moves, "the pointer is placed before scrolling"


def test_one_notch_takes_about_as_long_as_the_real_device_took():
    """Guards the transport-latency regression: `mouse.wheel` is an awaited CDP
    round-trip, so sleeping the step outright stretched the 166 ms cascade to
    317 ms live. Steps are scheduled against the clock instead."""
    clock = Clock()
    page = FakePage(clock.ms)
    sd.scroll_notch(page, rng=random.Random(1), sleep=clock.sleep, clock=clock,
                    platform="darwin")
    ts = [t for t, _dx, _dy in page.mouse.wheels]
    span = ts[-1] - ts[0]
    assert 130 <= span <= 210, span               # measured real notch: 166.4 ms
    gaps = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    assert 12 <= statistics.median(gaps) <= 19    # display cadence, not 33 ms
    assert len(set(gaps)) > 1                     # jittered, not a fixed tick


def test_scroll_up_is_negative():
    page, _clock, _n = _run(sd.reading_scroll, notches=2, direction=-1)
    assert all(dy < 0 for _t, _dx, dy in page.mouse.wheels)


def test_bursts_are_separated_by_much_longer_reading_pauses():
    page, _clock, _n = _run(sd.reading_scroll, notches=14)
    ts = [t for t, _dx, _dy in page.mouse.wheels]
    gaps = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    inside = [g for g in gaps if g < 40]           # within one notch cascade
    between = [g for g in gaps if g >= 40]
    assert inside and between
    assert max(inside) < min(between)
    assert statistics.median(between) > 50.0


def test_read_pause_is_right_skewed_and_capped():
    rng = random.Random(4)
    xs = [sd.read_pause_ms(rng) for _ in range(4000)]
    assert statistics.fmean(xs) > statistics.median(xs) * 1.15
    assert 600 <= statistics.median(xs) <= 1800
    assert max(xs) <= sd.READ_PAUSE_CAP_MS


def test_notch_count_varies_between_runs():
    counts = {sd.read_profile(FakePage(Clock()), rng=random.Random(s),
                              sleep=lambda _s: None) for s in range(30)}
    assert len(counts) > 1
    assert min(counts) >= sd.PROFILE_READ_NOTCHES[0]
    assert max(counts) <= sd.PROFILE_READ_NOTCHES[1]


def test_viewport_is_used_for_the_pointer_position_without_evaluating_js():
    page, _clock, _n = _run(sd.reading_scroll, notches=1,
                            viewport={"width": 1000, "height": 600})
    assert page.mouse.moves == [(500.0, 330.0)]


def test_missing_viewport_falls_back_without_touching_the_page():
    page, _clock, _n = _run(sd.reading_scroll, notches=1, viewport=None)
    assert page.mouse.moves == [(640.0, 400.0)]


def test_a_failing_page_never_breaks_the_action_that_asked_to_scroll():
    clock = Clock()
    page = FakePage(clock, viewport=None, explode=True)
    assert sd.reading_scroll(page, notches=5, rng=random.Random(1),
                             sleep=clock.sleep) == 0


def test_read_profile_and_read_results_scroll_different_amounts():
    assert sd.PROFILE_READ_NOTCHES[1] > sd.RESULTS_READ_NOTCHES[1]
    for fn in (sd.read_profile, sd.read_results):
        page, _clock, n = _run(fn)
        assert n > 0 and page.mouse.wheels
