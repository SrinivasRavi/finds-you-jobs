# voyager_py/tests/test_typing_dynamics.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Keystroke dynamics — the DISTRIBUTION, not just "the code runs".

Regression cover for the defect where `human_type` drew one delay per message
and handed it to Playwright's `delay=`, giving exactly zero inter-key variance
inside a message and a flat ~93 ms dwell. Every assertion here is about the
shape of the sampled sequence: non-zero variance, a median in the measured
band, a right-skewed tail (mean > median), and a floor no human can beat.

Targets come from `docs/internal/evidence/2026-08-06-focus-tab-probe/
research-digest.md` ("Typing"): at ~50 WPM median IKI 164 ms, mean 245 ms,
CV ~1.0, p5 68 ms; dwell median 105 ms, per-subject CV 0.243.
"""

# ruff: noqa: S311 — seeded PRNGs make these distribution assertions reproducible;
# nothing here is cryptographic.
from __future__ import annotations

import random
import statistics

import pytest

from sidecar.packages.referral_outreach.upstream import session
from sidecar.packages.referral_outreach.upstream.typing_dynamics import (
    DEFAULT_PERSONA,
    MIN_INTERVAL_MS,
    TypingPersona,
    digraph_class,
    needs_shift,
    persona_for_legacy_bounds,
    plan,
    sample_hold_ms,
    sample_interval_ms,
)

SAMPLE = (
    "Hi Dana, I'm applying to the Staff Backend role on your team and would "
    "value a quick referral if you think the fit is right. Happy to send my "
    "resume over. Thanks!"
)


def _rng():
    return random.Random(20260806)


# --- the interval distribution ---------------------------------------------
def test_intervals_have_real_variance_and_are_never_metronomic():
    rng = _rng()
    xs = [ks.iki_ms for ks in plan(SAMPLE * 3, DEFAULT_PERSONA, rng)][1:]
    assert len(set(round(x, 3) for x in xs)) > len(xs) * 0.95  # essentially all distinct
    assert statistics.pstdev(xs) > 50.0                        # the old value was 0.0


def test_interval_median_and_mean_match_the_50wpm_targets():
    rng = _rng()
    xs = [ks.iki_ms for ks in plan(SAMPLE * 12, DEFAULT_PERSONA, rng)][1:]
    median = statistics.median(xs)
    mean = statistics.fmean(xs)
    assert 130 <= median <= 200, median          # target 164 ms
    assert 200 <= mean <= 300, mean              # target 245 ms
    assert mean > median * 1.2                   # right-skewed, not symmetric
    cv = statistics.pstdev(xs) / mean
    assert 0.7 <= cv <= 1.4, cv                  # target CV ~1.0


def test_interval_percentiles_sit_in_the_measured_band():
    rng = _rng()
    xs = sorted(ks.iki_ms for ks in plan(SAMPLE * 40, DEFAULT_PERSONA, rng))[1:]
    q = statistics.quantiles(xs, n=100)
    p5, p25, p75, p90 = q[4], q[24], q[74], q[89]
    # Empirical p5 is 68 ms. No two-parameter log-normal (or log-logistic — both
    # were checked against the digest's full percentile list) reproduces a low
    # tail that thin without a point mass at a hard floor, and a point mass is
    # itself a tell ("too smooth" is its own signature, §17.3). We take the
    # slightly fatter low tail and keep the floor soft.
    assert 40 <= p5 <= 110, p5
    assert 90 <= p25 <= 150, p25      # target 115
    assert 200 <= p75 <= 320, p75     # target 255
    assert 350 <= p90 <= 620, p90     # target 476


def test_no_interval_is_below_the_human_floor():
    rng = _rng()
    xs = [ks.iki_ms for ks in plan(SAMPLE * 20, DEFAULT_PERSONA, rng)][1:]
    assert min(xs) >= MIN_INTERVAL_MS
    assert MIN_INTERVAL_MS >= 40  # the old code allowed 10 ms — >200 WPM


def test_intervals_are_not_uniform_nor_gaussian():
    """A right-skewed sample puts far more mass below the mean than above it."""
    rng = _rng()
    xs = [ks.iki_ms for ks in plan(SAMPLE * 30, DEFAULT_PERSONA, rng)][1:]
    mean = statistics.fmean(xs)
    below = sum(1 for x in xs if x < mean) / len(xs)
    assert below > 0.6, below         # uniform would be 0.5, Gaussian 0.5


# --- digraph structure ------------------------------------------------------
def test_digraph_classification():
    assert digraph_class("f", "j") == "alternation"      # left index -> right index
    assert digraph_class("f", "v") == "same_finger"      # both left index
    assert digraph_class("a", "s") == "same_hand"        # left pinky -> left ring
    assert digraph_class("s", "s") == "repetition"
    assert digraph_class(None, "a") == "unknown"
    assert digraph_class("a", " ") == "alternation"      # thumb


def test_same_finger_is_slower_than_same_hand_is_slower_than_alternation():
    n = 4000
    def med(prev, cur):
        rng = random.Random(7)
        return statistics.median(
            sample_interval_ms(prev, cur, DEFAULT_PERSONA, rng) for _ in range(n)
        )
    alt, same_hand, same_finger = med("f", "j"), med("a", "s"), med("f", "v")
    assert alt < same_hand < same_finger
    # measured medians 141 / 169 / 199 ms -> ratios 0.83 and 1.18
    assert 0.78 <= alt / same_hand <= 0.90
    assert 1.10 <= same_finger / same_hand <= 1.26


def test_punctuation_costs_extra():
    rng = random.Random(11)
    plain = statistics.median(sample_interval_ms("h", "e", DEFAULT_PERSONA, rng)
                              for _ in range(4000))
    rng = random.Random(11)
    punct = statistics.median(sample_interval_ms("h", ".", DEFAULT_PERSONA, rng)
                              for _ in range(4000))
    assert punct - plain >= 120, (plain, punct)


# --- dwell ------------------------------------------------------------------
def test_dwell_varies_and_matches_the_measured_median():
    rng = _rng()
    holds = [ks.hold_ms for ks in plan(SAMPLE * 12, DEFAULT_PERSONA, rng)]
    assert statistics.pstdev(holds) > 15.0        # the measured baseline was +-1.5 ms
    assert 90 <= statistics.median(holds) <= 135  # target 105 ms
    cv = statistics.pstdev(holds) / statistics.fmean(holds)
    assert 0.15 <= cv <= 0.40, cv                 # per-subject CV 0.243


def test_dwell_and_interval_are_not_coupled():
    rng = _rng()
    ks = plan(SAMPLE * 20, DEFAULT_PERSONA, rng)[1:]
    r = statistics.correlation([k.iki_ms for k in ks], [k.hold_ms for k in ks])
    assert abs(r) < 0.15, r                       # measured |r| < 0.13


def test_left_pinky_holds_longer_than_right_index():
    rng = random.Random(3)
    pinky = statistics.fmean(sample_hold_ms("a", DEFAULT_PERSONA, rng) for _ in range(4000))
    rng = random.Random(3)
    index = statistics.fmean(sample_hold_ms("j", DEFAULT_PERSONA, rng) for _ in range(4000))
    assert pinky - index >= 15


# --- persona ----------------------------------------------------------------
def test_faster_persona_types_faster():
    rng = _rng()
    slow = statistics.median(k.iki_ms for k in plan(SAMPLE * 6, TypingPersona(wpm=30), rng)[1:])
    rng = _rng()
    fast = statistics.median(k.iki_ms for k in plan(SAMPLE * 6, TypingPersona(wpm=80), rng)[1:])
    assert fast < slow * 0.6


def test_legacy_bounds_map_to_a_human_speed_not_a_uniform_band():
    p = persona_for_legacy_bounds(10, 50)
    assert 55 <= p.wpm <= 95              # "fast", not 240-1200 WPM
    assert persona_for_legacy_bounds(None, None) == DEFAULT_PERSONA
    # An unrecognised pair is still clamped into human range.
    assert 25 <= persona_for_legacy_bounds(1, 2).wpm <= 95


def test_needs_shift():
    assert needs_shift("A") and needs_shift("!") and needs_shift("?")
    assert not needs_shift("a") and not needs_shift(" ") and not needs_shift("1")


# --- the driver: session.human_type ----------------------------------------
class FakeKeyboard:
    def __init__(self, clock, unsupported=()):
        self.clock = clock
        self.unsupported = set(unsupported)
        self.log: list[tuple[str, str, float]] = []

    def down(self, key):
        if key in self.unsupported:
            raise ValueError(f"Unknown key: {key}")
        self.log.append(("down", key, self.clock()))

    def up(self, key):
        self.log.append(("up", key, self.clock()))


class FakePage:
    def __init__(self, keyboard):
        self.keyboard = keyboard


class FakeLocator:
    def __init__(self, clock, unsupported=()):
        self.keyboard = FakeKeyboard(clock, unsupported)
        self.page = FakePage(self.keyboard)
        self.focused = 0
        self.typed: list[str] = []

    def focus(self):
        self.focused += 1

    def type(self, text, **_kw):  # the non-ASCII fallback path
        self.typed.append(text)


class Clock:
    """A monotonic clock in SECONDS that only advances when slept on, so the
    schedule the driver plays out is exactly the schedule it planned."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def ms(self):
        return self.t * 1000.0

    def sleep(self, seconds):
        assert seconds >= 0
        self.t += seconds


def _drive(text, **kw):
    clock = Clock()
    loc = FakeLocator(clock.ms, kw.pop("unsupported", ()))
    session.human_type(loc, text, sleep=clock.sleep, clock=clock, rng=_rng(), **kw)
    return loc, clock


def test_human_type_dispatches_at_key_level_and_never_bulk_types():
    loc, _ = _drive("hello")
    assert loc.focused == 1
    assert loc.typed == []            # no locator.type(whole string), no insertText
    downs = [e for e in loc.keyboard.log if e[0] == "down"]
    assert [e[1] for e in downs] == list("hello")


def _holds_and_ikis(log):
    """Key-hold dwells and PRESS-TO-PRESS intervals, read off the driver's log."""
    holds, ikis = [], []
    last_down = None
    for i, (kind, key, t) in enumerate(log):
        if kind == "down" and key != "Shift":
            if last_down is not None:
                ikis.append(t - last_down)
            last_down = t
            up = next(tt for k, kk, tt in log[i + 1:] if k == "up" and kk == key)
            holds.append(up - t)
    return holds, ikis


def test_human_type_produces_varying_dwell_and_varying_press_to_press():
    loc, _ = _drive(SAMPLE)
    holds, ikis = _holds_and_ikis(loc.keyboard.log)
    assert statistics.pstdev(holds) > 15.0
    assert statistics.pstdev(ikis) > 40.0
    assert statistics.fmean(ikis) > statistics.median(ikis)   # right-skewed
    assert min(ikis) > 0


def test_driver_press_to_press_tracks_the_model_not_model_plus_dwell():
    """The regression this guards: treating the sampled interval as an UP->DOWN
    gap silently adds the whole dwell to every press-to-press latency, inflating
    a 165 ms median to ~270 ms. IKI is press-to-press in every corpus we cite."""
    loc, _ = _drive(SAMPLE * 4, min_delay=50, max_delay=200)  # the 50 WPM persona
    _holds, ikis = _holds_and_ikis(loc.keyboard.log)
    assert 130 <= statistics.median(ikis) <= 215, statistics.median(ikis)


def test_driver_never_inverts_two_presses():
    loc, _ = _drive(SAMPLE * 2)
    log = loc.keyboard.log
    downs = [t for k, key, t in log if k == "down" and key != "Shift"]
    assert downs == sorted(downs)
    # ...and the previous key is always released before the next is pressed
    # (rollover is item 1.2 and is deliberately absent here).
    _holds, ikis = _holds_and_ikis(log)
    assert min(ikis) >= 8.0


def test_shift_is_held_across_the_letter_it_modifies():
    loc, _ = _drive("aAb")
    log = loc.keyboard.log
    shift_down = next(t for k, key, t in log if k == "down" and key == "Shift")
    shift_up = next(t for k, key, t in log if k == "up" and key == "Shift")
    a_down = next(t for k, key, t in log if k == "down" and key == "A")
    a_up = next(t for k, key, t in log if k == "up" and key == "A")
    assert shift_down < a_down < a_up <= shift_up
    # ...and the modified letter's own dwell is a normal dwell, not stretched.
    assert 20 <= (a_up - a_down) <= 400


def test_shift_is_not_pressed_for_unshifted_characters():
    loc, _ = _drive("hello world")
    assert not any(key == "Shift" for _k, key, _t in loc.keyboard.log)


def test_non_ascii_falls_back_per_character_and_releases_shift():
    loc, _ = _drive("aXb", unsupported={"X"})
    assert loc.typed == ["X"]
    # Shift was taken down for the capital, then released before the fallback.
    keys = [(k, key) for k, key, _t in loc.keyboard.log]
    assert ("down", "Shift") in keys
    assert keys.count(("up", "Shift")) == keys.count(("down", "Shift"))
    assert ("down", "X") not in keys


def test_empty_text_is_a_no_op_beyond_focus():
    loc, _ = _drive("")
    assert loc.focused == 1 and loc.keyboard.log == [] and loc.typed == []


@pytest.mark.parametrize("bounds", [(10, 50), (50, 200), (None, None)])
def test_existing_call_sites_still_work(bounds):
    loc, _ = _drive("Referral request?", min_delay=bounds[0], max_delay=bounds[1])
    assert [e[1] for e in loc.keyboard.log if e[0] == "down" and e[1] != "Shift"] == list(
        "Referral request?"
    )
