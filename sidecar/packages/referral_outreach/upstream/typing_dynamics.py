# voyager_py/typing_dynamics.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW code written for the finds-you-jobs fork (GPL, lives in the GPL subtree).
# Not derived from OpenOutreach: upstream has no keystroke-dynamics model at all
# (`browser/nav.py` @ a7a9101 types with a single Playwright `delay=` value).
"""Keystroke dynamics: per-keystroke inter-key interval and key-hold dwell.

Why this exists. `session.human_type` used to draw ONE delay per message and
hand it to Playwright's `locator.type(text, delay=…)`, which applies the same
pause between every keypress — inter-key variance within a message was exactly
zero, and key-hold dwell was a flat ~93 ms. Both are one-dimensional
discriminators over a ~70-keystroke message. See `docs/internal/embedded-
browser.md` §15.3/§17.2 and the parameter targets in
`docs/internal/evidence/2026-08-06-focus-tab-probe/research-digest.md`.

The model, and where each number comes from (all in the digest's "Typing" table,
computed from the CMU `DSL-StrongPasswordData` and Aalto 136M corpora):

* **Inter-key interval (IKI)** is right-skewed, never Gaussian and never
  uniform. We sample log-normal (the accepted approximation to the best-fitting
  log-logistic). At ~50 WPM: mu 5.195, sigma 0.701 → median 164 ms, mean 245 ms,
  CV ~1.0, p90 476 ms.
* **Key-hold dwell** is separately log-normal: mu 4.652 → median 105 ms. The
  digest's sigma 0.416 is the ACROSS-SUBJECT fit (CV 0.435); we simulate one
  typist, so we use the per-subject CV of 0.243. Hold and interval are
  near-independent (|r| < 0.13), so they are sampled independently on purpose.
* **Digraph class** shifts the IKI median: hand alternation 141 ms <
  same-hand-different-finger 169 ms < same-finger 199 ms (repetition 155 ms).
  Implemented as a multiplicative factor on the sampled interval so the shape is
  preserved.
* **Punctuation** costs an extra 150-250 ms on the transition INTO it.
* **Shift is held across the letter it modifies** (median hold 240 ms) and the
  modified letter's own dwell is unchanged. Pressing Shift like an ordinary key
  is a recognisable artifact.

Everything here is pure arithmetic over `random` — no I/O, no clock. The caller
(`session.human_type`) owns the sleeping, so this module stays trivially
testable and can never block the event loop.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# --- population anchors (Dhakal CHI 2018, n=168,960) ------------------------
POPULATION_WPM_MEAN = 51.56
POPULATION_WPM_SD = 20.20

# Log-normal parameters of the inter-key interval at the population-mean speed.
# median = exp(mu); mean = exp(mu + sigma^2/2).
IKI_MU_AT_50WPM = 5.195
IKI_SIGMA = 0.701

# Log-normal parameters of key-hold dwell. Independent of the interval.
# The digest records BOTH a population fit (sigma 0.416, i.e. CV 0.435 across
# subjects) and a per-subject CV of 0.243. We are simulating ONE typist, so the
# per-subject spread is the correct one; sigma = sqrt(ln(1 + CV^2)).
HOLD_MU = 4.652
HOLD_SIGMA_POPULATION = 0.416
HOLD_SIGMA = math.sqrt(math.log(1 + 0.243**2))  # ~0.2395, CV 0.243

# Shift is a modifier, not a key in the stream: it goes down before the letter
# and comes up after it, spanning the whole keypress.
SHIFT_HOLD_MU = 5.48       # median ~240 ms
SHIFT_HOLD_SIGMA = 0.60

# The floor. The old code allowed 10 ms, which is >200 WPM — superhuman, and a
# free giveaway. p5 of the measured distribution at 50 WPM is 68 ms; we clamp a
# little below that so the tail is not truncated into a spike.
MIN_INTERVAL_MS = 45
MAX_INTERVAL_MS = 4000     # a longer pause is a "thinking" event, not a keystroke
MIN_HOLD_MS = 28
MAX_HOLD_MS = 400

# Digraph-class multipliers, from the measured medians (141/155/169/199 ms)
# normalised to the same-hand-different-finger case that the base mu describes.
DIGRAPH_FACTOR = {
    "alternation": 141 / 169,
    "repetition": 155 / 169,
    "same_hand": 1.0,
    "same_finger": 199 / 169,
    "unknown": 1.0,
}

PUNCTUATION = set(".,;:!?'\"()[]{}<>/\\|-_=+*&^%$#@~`")
PUNCTUATION_PENALTY_MS = (150, 250)

# --- keyboard geometry (US QWERTY) -----------------------------------------
# Only enough structure to classify a digraph as same-finger / same-hand /
# alternation. Deliberately not a full biomechanical model — the digest's note
# is explicit that a simple classification is enough and that over-engineering
# ("too smooth") is its own signature.
_ROWS = [
    "`1234567890-=",
    "qwertyuiop[]\\",
    "asdfghjkl;'",
    "zxcvbnm,./",
]
# finger id per column-ish position: 0-3 left (pinky..index), 4-7 right
# (index..pinky). Index fingers cover two columns, which is what produces most
# same-finger transitions.
_FINGER_BY_CHAR: dict[str, int] = {}


def _build_finger_map() -> None:
    layouts = [
        ("`1qaz", 0), ("2wsx", 1), ("3edc", 2), ("4rfv5tgb", 3),
        ("6yhn7ujm", 4), ("8ik,", 5), ("9ol.", 6), ("0p;/-=[]'\\", 7),
    ]
    for chars, finger in layouts:
        for ch in chars:
            _FINGER_BY_CHAR[ch] = finger


_build_finger_map()
# The shifted twin of every mapped key lands on the same finger.
_SHIFT_PAIRS = {
    "~": "`", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
    "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[",
    "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
}
for _shifted, _base in _SHIFT_PAIRS.items():
    if _base in _FINGER_BY_CHAR:
        _FINGER_BY_CHAR[_shifted] = _FINGER_BY_CHAR[_base]


def _finger(ch: str) -> int | None:
    return _FINGER_BY_CHAR.get(ch.lower())


def digraph_class(prev: str | None, cur: str) -> str:
    """Classify the transition `prev` → `cur` for IKI scaling."""
    if prev is None:
        return "unknown"
    if cur == " " or prev == " ":
        # The space bar is thumb-operated: it alternates with everything.
        return "alternation"
    if prev.lower() == cur.lower():
        return "repetition"
    fa, fb = _finger(prev), _finger(cur)
    if fa is None or fb is None:
        return "unknown"
    if fa == fb:
        return "same_finger"
    return "same_hand" if (fa < 4) == (fb < 4) else "alternation"


# --- the persona -----------------------------------------------------------
@dataclass(frozen=True)
class TypingPersona:
    """One typist. `wpm` scales the interval; everything else follows from it.

    `rollover_ratio` is consumed by the rollover model (item 1.2) and lives here
    so a persona is one object, not two.
    """

    wpm: float = POPULATION_WPM_MEAN
    iki_sigma: float = IKI_SIGMA
    hold_sigma: float = HOLD_SIGMA
    rollover_ratio: float = 0.25

    @property
    def iki_mu(self) -> float:
        # Interval scales inversely with speed: mu shifts by log(50/wpm).
        return IKI_MU_AT_50WPM + math.log(50.0 / max(self.wpm, 5.0))

    @property
    def hold_mu(self) -> float:
        # Faster typists hold marginally shorter: ~-0.3 ms per +1 WPM around 50.
        median = math.exp(HOLD_MU) - 0.3 * (self.wpm - 50.0)
        return math.log(max(median, 35.0))


DEFAULT_PERSONA = TypingPersona()

# `human_type`'s legacy signature took raw uniform millisecond bounds
# (`actions.py` passes 10, 50). Those are re-read as a SPEED PERSONA: the old
# bounds said "type fast", so they map to a fast-but-human typist rather than to
# a literal 10-50 ms uniform draw, which is 240-1200 WPM.
_LEGACY_BOUND_WPM = {
    (10, 50): 68.0,     # the compose/note path — "as fast as this typist goes"
    (50, 200): 51.56,   # the module defaults — the population mean
}


def persona_for_legacy_bounds(min_ms: int | None, max_ms: int | None) -> TypingPersona:
    """Map the old `min_delay`/`max_delay` millisecond pair to a persona.

    Keeps every existing call site working without pretending the old numbers
    were ever realistic keystroke intervals.
    """
    if min_ms is None and max_ms is None:
        return DEFAULT_PERSONA
    lo = 50 if min_ms is None else min_ms
    hi = 200 if max_ms is None else max_ms
    wpm = _LEGACY_BOUND_WPM.get((lo, hi))
    if wpm is None:
        # Unknown pair: treat the midpoint as the old uniform mean and translate
        # it to a WPM, floored at a human speed rather than trusted verbatim.
        mid_ms = max((lo + hi) / 2.0, 1.0)
        wpm = min(max(12000.0 / mid_ms, 25.0), 95.0)
    rollover = min(0.70, max(0.05, 0.25 + 0.0073 * (wpm - 50.0) * 2))
    return TypingPersona(wpm=wpm, rollover_ratio=rollover)


# --- samplers --------------------------------------------------------------
def _lognormal(mu: float, sigma: float, rng: random.Random) -> float:
    return math.exp(rng.gauss(mu, sigma))


def sample_interval_ms(
    prev: str | None,
    cur: str,
    persona: TypingPersona = DEFAULT_PERSONA,
    rng: random.Random | None = None,
) -> float:
    """One inter-key interval, in milliseconds, for the transition prev → cur."""
    rng = rng or random
    raw = _lognormal(persona.iki_mu, persona.iki_sigma, rng)
    raw *= DIGRAPH_FACTOR[digraph_class(prev, cur)]
    if cur in PUNCTUATION:
        raw += rng.uniform(*PUNCTUATION_PENALTY_MS)
    return min(max(raw, MIN_INTERVAL_MS), MAX_INTERVAL_MS)


def sample_hold_ms(
    ch: str,
    persona: TypingPersona = DEFAULT_PERSONA,
    rng: random.Random | None = None,
) -> float:
    """One key-hold dwell, in milliseconds. Independent of the interval."""
    rng = rng or random
    raw = _lognormal(persona.hold_mu, persona.hold_sigma, rng)
    finger = _finger(ch)
    if finger == 0:            # left pinky is measurably slower: +25 ms vs right index
        raw += 25.0
    elif finger is not None and finger < 4:
        raw += 8.5             # left hand overall
    return min(max(raw, MIN_HOLD_MS), MAX_HOLD_MS)


def sample_shift_hold_ms(rng: random.Random | None = None) -> float:
    """How long Shift stays down. It SPANS the letter it modifies."""
    rng = rng or random
    return min(max(_lognormal(SHIFT_HOLD_MU, SHIFT_HOLD_SIGMA, rng), 90.0), 900.0)


def needs_shift(ch: str) -> bool:
    """True when a US-QWERTY typist would hold Shift to produce `ch`."""
    return ch.isupper() or ch in _SHIFT_PAIRS


@dataclass(frozen=True)
class Keystroke:
    """One planned keypress. All times are milliseconds.

    `iki_ms` is the **press-to-press** latency from the previous keystroke's
    key-down to this one's — that is the quantity every corpus in the digest
    reports (Dhakal's IKI, the p5/p50/p90 percentiles, the digraph medians). It
    is NOT the gap between the previous key-up and this key-down. The
    distinction is load-bearing: when `iki_ms` is shorter than the previous
    key's `hold_ms` the two presses genuinely overlap, which is exactly what
    keystroke rollover is.

    `hold_ms` is this key's own down→up dwell.
    """

    char: str
    iki_ms: float
    hold_ms: float
    shift: bool = False
    shift_hold_ms: float = 0.0


def plan(
    text: str,
    persona: TypingPersona = DEFAULT_PERSONA,
    rng: random.Random | None = None,
) -> list[Keystroke]:
    """Turn a message into a per-keystroke schedule. Pure; no sleeping."""
    rng = rng or random
    out: list[Keystroke] = []
    prev: str | None = None
    for ch in text:
        shift = needs_shift(ch)
        out.append(
            Keystroke(
                char=ch,
                iki_ms=0.0 if prev is None else sample_interval_ms(prev, ch, persona, rng),
                hold_ms=sample_hold_ms(ch, persona, rng),
                shift=shift,
                shift_hold_ms=sample_shift_hold_ms(rng) if shift else 0.0,
            )
        )
        prev = ch
    return out
