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
browser.md` section 15.3/section 17.2 and the parameter targets in
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
def target_rollover_ratio(wpm: float) -> float:
    """The measured rollover ratio at a given speed.

    Corpus points: 7% @25 WPM, 24% @50, 39% @80, 52% @95 — near-linear in WPM
    (r with WPM is +0.73), so a line through them is the honest fit. Never 0:
    only 2 of 51 subjects in one corpus produced none, which makes a ratio of
    exactly 0.000 a one-dimensional discriminator over ~70 keystrokes.
    """
    return min(0.70, max(0.02, 0.0064 * wpm - 0.09))


@dataclass(frozen=True)
class TypingPersona:
    """One typist. `wpm` scales the interval; everything else follows from it.

    `rollover_ratio` is the fraction of transitions on which the next key goes
    down before the previous one comes up. `None` derives it from `wpm`; set it
    explicitly to pin a persona.
    """

    wpm: float = POPULATION_WPM_MEAN
    iki_sigma: float = IKI_SIGMA
    hold_sigma: float = HOLD_SIGMA
    rollover_ratio: float | None = None

    @property
    def rollover(self) -> float:
        return (target_rollover_ratio(self.wpm) if self.rollover_ratio is None
                else self.rollover_ratio)

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
    return TypingPersona(wpm=wpm)


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


# --- rollover ---------------------------------------------------------------
# Rollover is not a separate effect bolted on: it is what happens when the
# press-to-press interval is SHORTER than the previous key's hold, which the
# two independent distributions already produce on their own. Sampling them
# per keystroke therefore yields overlaps whose magnitude needs no tuning
# (measured on our own sampler: median 37 ms, mean 43, p90 84 against corpus
# targets of 39 / 59 / 95). What DOES need a knob is the RATE, because the raw
# emergent rate runs a few points above the corpus: 0.31 at 50 WPM against a
# measured 0.24. So an emergent overlap is accepted with probability
# `target / emergent`, where `emergent` is estimated once per persona.
MAX_OVERLAP_MS = 250.0
_EMERGENT_SAMPLES = 6000
_emergent_cache: dict[tuple[float, float, float], float] = {}


def emergent_rollover_ratio(persona: TypingPersona) -> float:
    """P(interval < previous hold) for this persona, by cached Monte Carlo.

    Analytic evaluation is possible for the bare log-normals but wrong once the
    digraph factors, the punctuation penalty and the floor are applied — those
    move it by ~8 points — so this measures the sampler we actually ship.
    """
    key = (persona.wpm, persona.iki_sigma, persona.hold_sigma)
    hit = _emergent_cache.get(key)
    if hit is not None:
        return hit
    rng = random.Random(0x5011)  # fixed: an estimate must not wobble per call
    letters = "the quick brown fox jumps over a lazy dog "
    overlaps = 0
    prev = letters[0]
    for i in range(1, _EMERGENT_SAMPLES):
        cur = letters[i % len(letters)]
        if sample_interval_ms(prev, cur, persona, rng) < sample_hold_ms(prev, persona, rng):
            overlaps += 1
        prev = cur
    ratio = overlaps / (_EMERGENT_SAMPLES - 1)
    _emergent_cache[key] = ratio
    return ratio


def rollover_acceptance(persona: TypingPersona) -> float:
    """How often an emergent overlap is allowed to stand, to hit the target."""
    emergent = emergent_rollover_ratio(persona)
    if emergent <= 0:
        return 1.0
    return min(1.0, persona.rollover / emergent)


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

    `hold_ms` is this key's own down→up dwell. `may_roll_over` says this press
    is allowed to land before the PREVIOUS key is released.

    `key` is what the driver should hand the keyboard; `dispatchable` is False
    for characters with no key definition, which the driver types one at a time
    through the locator instead.
    """

    char: str
    iki_ms: float
    hold_ms: float
    shift: bool = False
    shift_hold_ms: float = 0.0
    may_roll_over: bool = False
    dispatchable: bool = True

    @property
    def key(self) -> str:
        return _KEY_ALIASES.get(self.char, self.char)


# Characters that map to a named key rather than to themselves. `locator.type`
# does the same thing, so behaviour on these is unchanged.
_KEY_ALIASES = {"\n": "Enter", "\r": "Enter", "\t": "Tab"}


def is_key_dispatchable(ch: str) -> bool:
    """True when the browser keyboard has a key definition for `ch`.

    Printable ASCII (plus newline/tab) is exactly the covered set. Anything else
    — accents, CJK, emoji — has no key and must go through the locator, one
    character at a time. `Input.insertText` and setting `.value` are never an
    option: both emit `input` with no key events at all, a documented tell found
    in 3 of 7 commercial agents.
    """
    return ch in _KEY_ALIASES or (ch.isascii() and ch.isprintable())


def plan(
    text: str,
    persona: TypingPersona = DEFAULT_PERSONA,
    rng: random.Random | None = None,
) -> list[Keystroke]:
    """Turn a message into a per-keystroke schedule. Pure; no sleeping."""
    rng = rng or random
    accept = rollover_acceptance(persona)
    out: list[Keystroke] = []
    prev: str | None = None
    prev_hold = 0.0
    for ch in text:
        shift = needs_shift(ch)
        iki = 0.0 if prev is None else sample_interval_ms(prev, ch, persona, rng)
        hold = sample_hold_ms(ch, persona, rng)
        # An overlap is available whenever this press is due before the previous
        # release. Whether it is taken is the persona's rollover rate.
        overlaps = prev is not None and iki < prev_hold
        out.append(
            Keystroke(
                char=ch,
                iki_ms=iki,
                hold_ms=hold,
                shift=shift,
                shift_hold_ms=sample_shift_hold_ms(rng) if shift else 0.0,
                may_roll_over=overlaps and rng.random() < accept,
                dispatchable=is_key_dispatchable(ch),
            )
        )
        prev = ch
        prev_hold = hold
    return out


# --- scheduling -------------------------------------------------------------
# A slow CDP round-trip must never be able to invert two presses, so
# non-overlapping keystrokes keep a small guaranteed separation.
MIN_PRESS_SEPARATION_MS = 8.0
# Shift's release trails the letter it modifies by this share of its remaining
# hold; the rest of the hold was spent leading into the letter.
_SHIFT_TRAIL = 0.7
_SHIFT_LEAD = 0.3


@dataclass(frozen=True)
class KeyEvent:
    """One thing the driver does, at `t_ms` on the message's own timeline.

    `action` is `down` / `up` (key-level dispatch) or `type` (the per-character
    locator fallback for characters with no key definition).
    """

    t_ms: float
    action: str
    key: str


def schedule(keystrokes: list[Keystroke]) -> list[KeyEvent]:
    """Lay a keystroke plan onto one time-ordered event stream.

    This is where rollover physically happens: when a keystroke is marked
    `may_roll_over`, its key-down is NOT pushed past the previous key-up, so the
    stream reads `down:x → down:y → up:x → up:y`. Overlap is capped at
    `MAX_OVERLAP_MS`.

    Shift is held continuously across a RUN of consecutive shifted characters —
    a person typing "ABC" or "!?" does not tap Shift three times — and its
    press spans the letters it modifies.
    """
    events: list[tuple[float, int, str, str]] = []
    seq = 0
    t_down_prev: float | None = None
    t_up_prev = 0.0
    shift_open = False
    shift_hold_ms = 0.0

    for i, ks in enumerate(keystrokes):
        if t_down_prev is None:
            t_down = 0.0
        else:
            t_down = t_down_prev + ks.iki_ms
            floor = (t_up_prev - MAX_OVERLAP_MS if ks.may_roll_over
                     else t_up_prev + MIN_PRESS_SEPARATION_MS)
            t_down = max(t_down, floor)
        t_up = t_down + ks.hold_ms

        if ks.shift and not shift_open:
            shift_hold_ms = ks.shift_hold_ms
            lead = min(shift_hold_ms * _SHIFT_LEAD, max(shift_hold_ms - ks.hold_ms, 0.0))
            events.append((max(t_down - lead, 0.0), seq, "down", "Shift"))
            seq += 1
            shift_open = True

        if ks.dispatchable:
            events.append((t_down, seq, "down", ks.key))
            seq += 1
            events.append((t_up, seq, "up", ks.key))
            seq += 1
        else:
            events.append((t_down, seq, "type", ks.char))
            seq += 1

        run_continues = i + 1 < len(keystrokes) and keystrokes[i + 1].shift
        if shift_open and not run_continues:
            trail = max(shift_hold_ms - ks.hold_ms, 0.0) * _SHIFT_TRAIL
            events.append((t_up + trail, seq, "up", "Shift"))
            seq += 1
            shift_open = False

        t_down_prev, t_up_prev = t_down, t_up

    events.sort(key=lambda e: (e[0], e[1]))
    return [KeyEvent(t, action, key) for t, _s, action, key in events]
