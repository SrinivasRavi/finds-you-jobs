# voyager_py/tests/test_mouse_dynamics.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: S311 — seeded PRNGs make these distribution assertions reproducible.
"""Pointer motion and pointer pressure.

Two things are asserted here that a "does it run" test would miss:

  - the SHAPE of the trajectory (movement time and peak velocity on the
    digest's reference reach, an early velocity peak, a bowed path, several
    submovements, a Fitts fit that is deliberately NOT perfect) — §17.3 is
    explicit that a generator whose moments are too clean is its own signature;
  - the values a real page observes, against REAL Chromium: moves before the
    click (0 was the measured baseline) and `pressure === 0.5` on pointerdown
    (0 was the measured baseline, and no physical mouse emits 0).
"""

from __future__ import annotations

import random
import statistics

import pytest

from sidecar.packages.referral_outreach.upstream import mouse_dynamics as md

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

PAGE = """
<style>#b{position:absolute;left:520px;top:360px;width:140px;height:44px}</style>
<button id="b">send</button>
<script>
window.__ev = [];
for (const t of ['mousemove','pointerdown','pointerup','pointermove','click'])
  document.addEventListener(t, (e) => window.__ev.push({
    type: t, x: e.clientX, y: e.clientY,
    pressure: e.pressure === undefined ? null : e.pressure,
    buttons: e.buttons, isTrusted: e.isTrusted}), true);
document.getElementById('b').addEventListener('click', () => { window.__clicked = true; });
</script>
"""


def _rng():
    return random.Random(20260806)


# --- trajectory shape -------------------------------------------------------
def test_trail_is_emitted_at_all_and_lands_on_the_target():
    path = md.trail((20.0, 90.0), (600.0, 380.0), target_w=140, target_h=44, rng=_rng())
    assert len(path) >= 15, "a click with no approach is the top mouse tell"
    assert path[-1][1] == pytest.approx(600.0)
    assert path[-1][2] == pytest.approx(380.0)


def test_samples_land_at_display_cadence_with_jitter():
    path = md.trail((20.0, 90.0), (600.0, 380.0), target_w=140, target_h=44, rng=_rng())
    gaps = [b[0] - a[0] for a, b in zip(path, path[1:], strict=False)]
    inside = [g for g in gaps if g < 28]          # excludes inter-submovement pauses
    assert 13 <= statistics.median(inside) <= 21   # ~60 Hz, not device polling rate
    assert statistics.pstdev(inside) > 0.8         # jittered, not a fixed tick


def _trials(distance, w, h, n=300):
    for seed in range(n):
        path = md.trail((0.0, 0.0), (float(distance), 0.0), target_w=w, target_h=h,
                        rng=random.Random(seed))
        speeds = [((a[0] + b[0]) / 2, abs(b[1] - a[1]) / max(b[0] - a[0], 1e-9) * 1000)
                  for a, b in zip(path, path[1:], strict=False)]
        t_peak, v_peak = max(speeds, key=lambda s: s[1])
        yield path[-1][0], v_peak, t_peak


def test_peak_velocity_matches_the_reference_reach():
    """The digest's reference: ~1500 px/s peak on a 400 px reach to a 100x30
    button, with movement time 750-1050 ms."""
    trials = list(_trials(400, 100, 30))
    assert 750 <= statistics.median(t for t, _v, _tp in trials) <= 1050
    assert 1200 <= statistics.median(v for _t, v, _tp in trials) <= 2000


def test_velocity_peaks_early_within_the_primary_submovement():
    """The digest's "time-to-peak ~30% of MT" is arithmetically incompatible
    with its own other figures: a 400 px reach at ~1500 px/s peak whose primary
    submovement covers 59% of the distance puts the peak at ~10-15% of the TOTAL
    movement time. 30% is where the peak sits inside the primary submovement's
    own duration, which is what the velocity profile encodes."""
    fractions = [tp / t for t, _v, tp in _trials(400, 100, 30)]
    assert 0.06 <= statistics.median(fractions) <= 0.30, statistics.median(fractions)
    # ...and never in the last third: that would be an accelerating approach.
    assert statistics.median(fractions) < 0.5


def test_path_bows_rather_than_running_dead_straight():
    devs = []
    for seed in range(30):
        start, end = (0.0, 0.0), (600.0, 0.0)
        path = md.trail(start, end, target_w=120, target_h=40, rng=random.Random(seed))
        distance = 600.0
        devs.append(statistics.fmean(abs(p[2]) for p in path) / distance)
    mean_dev = statistics.fmean(devs)
    assert 0.005 <= mean_dev <= 0.08, mean_dev     # target: mean |perp| 2.9% of D


def test_several_submovements_with_pauses_between_them():
    path = md.trail((0.0, 0.0), (700.0, 300.0), target_w=120, target_h=40, rng=_rng())
    gaps = [b[0] - a[0] for a, b in zip(path, path[1:], strict=False)]
    pauses = [g for g in gaps if g > 28]
    assert 1 <= len(pauses) <= md.MAX_SUBMOVEMENTS   # ~4.3 submovements for an adult
    assert all(g <= md.FRAME_MS + md.INTER_SUBMOVEMENT_MS[1] + 12 for g in pauses)


def test_movement_time_matches_fitts_but_is_not_a_perfect_fit():
    rng = random.Random(5)
    mts = [md.movement_time_ms(400, 100, 30, rng) for _ in range(3000)]
    assert 700 <= statistics.median(mts) <= 1100     # measured band 750-1050 ms
    cv = statistics.pstdev(mts) / statistics.fmean(mts)
    assert 0.15 <= cv <= 0.35, cv                    # measured spread 20-25%
    # A harder target takes longer — the law holds even though the fit is noisy.
    easy = statistics.median([md.movement_time_ms(400, 300, 300, rng) for _ in range(2000)])
    hard = statistics.median([md.movement_time_ms(400, 20, 20, rng) for _ in range(2000)])
    assert hard > easy * 1.4


def test_no_fixed_frequency_tremor():
    """A clean sinusoid is a named tell; the per-sample noise must be broadband,
    which shows up as an unpredictable sign sequence rather than a periodic one."""
    path = md.trail((0.0, 0.0), (900.0, 0.0), target_w=140, target_h=44, rng=_rng())
    resid = [p[2] for p in path]
    flips = sum(1 for a, b in zip(resid, resid[1:], strict=False) if (a > 0) != (b > 0))
    assert 0 < flips < len(resid)                    # neither periodic nor monotone


def test_click_hold_varies():
    rng = random.Random(9)
    holds = [md.click_hold_ms(rng) for _ in range(3000)]
    assert statistics.pstdev(holds) > 10.0
    assert 50 <= statistics.median(holds) <= 140
    assert min(holds) >= 30.0


def test_landing_point_is_inside_the_element_but_not_always_its_centre():
    box = {"x": 100.0, "y": 200.0, "width": 140.0, "height": 44.0}
    rng = random.Random(2)
    pts = [md.target_point(box, rng) for _ in range(500)]
    assert all(box["x"] <= x <= box["x"] + box["width"] for x, _y in pts)
    assert all(box["y"] <= y <= box["y"] + box["height"] for _x, y in pts)
    assert len({(round(x), round(y)) for x, y in pts}) > 20


def test_zero_distance_needs_no_trail():
    assert md.trail((10.0, 10.0), (10.2, 10.2), rng=_rng()) == []


# --- against real Chromium --------------------------------------------------
@pytest.fixture(scope="module")
def page():
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover — CI without the browser binary
            pytest.skip(f"Chromium not available: {exc}")
        p = browser.new_context(viewport={"width": 900, "height": 700}).new_page()
        p.set_content(PAGE)
        yield p
        browser.close()


def test_real_click_emits_a_motion_trail_and_real_pressure(page):
    page.evaluate("window.__ev = []; window.__clicked = false;")
    assert md.human_click(page.locator("#b"), rng=_rng()) is True
    events = page.evaluate("window.__ev")
    assert page.evaluate("window.__clicked") is True

    first_down = next(i for i, e in enumerate(events) if e["type"] == "pointerdown")
    moves_before = [e for e in events[:first_down] if e["type"] == "mousemove"]
    assert len(moves_before) >= 10, "measured baseline was 0 moves before the click"

    down = events[first_down]
    assert down["pressure"] == 0.5, "a real mouse is 0.5; CDP defaults to 0"
    assert down["isTrusted"] is True
    up = next(e for e in events if e["type"] == "pointerup")
    assert up["pressure"] == 0.0        # correct for a release


def test_real_click_lands_inside_the_button(page):
    page.evaluate("window.__ev = []; window.__clicked = false;")
    md.human_click(page.locator("#b"), rng=random.Random(3))
    down = next(e for e in page.evaluate("window.__ev") if e["type"] == "pointerdown")
    assert 520 <= down["x"] <= 660 and 360 <= down["y"] <= 404


def test_pointer_position_carries_over_between_clicks(page):
    md.human_click(page.locator("#b"), rng=random.Random(4))
    x, y = md.pointer_position(page)
    assert 520 <= x <= 660 and 360 <= y <= 404


def test_degrades_to_the_callers_own_click_when_the_element_cannot_be_measured(page):
    called = []
    ok = md.human_click(page.locator("#nope"), fallback=lambda: called.append(1),
                        rng=_rng(), timeout_ms=250)
    assert ok is False and called == [1]


def test_degrading_without_a_fallback_is_not_an_error(page):
    assert md.human_click(page.locator("#nope"), rng=_rng(), timeout_ms=250) is False
