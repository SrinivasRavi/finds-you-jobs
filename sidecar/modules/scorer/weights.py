"""How per-axis factors become one 0-to-100 number, in one place.

A method emits typed factors (`types.Factor`); this turns them into the score the
board sorts on. 2 rules the arithmetic obeys, both from `scoring.md` section 7.4:

- **An axis the method could not assess drops out of the weighting**, and the
  remaining weights renormalize. Scoring an unassessed axis as 0 would punish a
  posting for what it failed to say.
- **A blocker is a flag, not a weight.** Work authorization and the like are
  reported beside the score and never deflate it, because the score ranks skill
  fit and the obstacle is its own axis (rank, don't gate).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .types import Factor

# Jev's axis set, and the first method whose total is computed from its factors
# rather than asked for directly.
JEV_WEIGHTS: Mapping[str, float] = {
    "hard_requirements": 0.30,
    "core_stack": 0.30,
    "years": 0.15,
    "level": 0.15,
    "nice_to_haves": 0.10,
}

# Deliberately NOT the same table as the keyword scorer's 55/30/15. That one was
# tuned to sit near the 60/40 it shipped with, so an existing user's board does
# not jump when they update, and it is the version measured at Spearman +0.484
# (docs/internal/scoring-build.md, T6b). The 2 tables unify when the user-facing
# weights land and every method's total is recomputed on read anyway.
KEYWORD_WEIGHTS: Mapping[str, float] = {
    "core_stack": 0.55,
    "years": 0.30,
    "level": 0.15,
}

# Points subtracted for a location the user did not ask for. The LLM path is
# instructed to apply -10 to -20 after banding, so every method takes the middle
# of that range and the same trap ranks the same way whichever method ran.
LOCATION_MISS_PENALTY = 15

# Axes that modify the total instead of being weighted into it.
MODIFIER_AXES = frozenset({"location_preference", "logistics"})


def weighted_total(factors: Sequence[Factor], weights: Mapping[str, float]) -> int:
    """0 to 100 from the factors present, renormalized, then the modifiers."""
    by_key = {f.factor: f for f in factors}
    present = {key: w for key, w in weights.items() if key in by_key}
    if not present:
        return 0
    scale = sum(present.values())
    total = sum(by_key[key].value * weight for key, weight in present.items()) / scale * 100

    location = by_key.get("location_preference")
    if location is not None and location.value < 0.5:
        total -= LOCATION_MISS_PENALTY
    return max(0, min(100, round(total)))


# --- user weights (scoring.md 7.4) -------------------------------------------
#
# One table over the 5 GRADED axes, applied to whatever factors a job has and
# renormalized over the ones present, so it works whichever method scored it.
# The 2 modifier axes are deliberately not in it: location and logistics are
# answered as confidences by Jev, and multiplying a confidence by a weight
# produces a number with no meaning (talk.md D43). Location gets its own dial
# instead, the size of the miss penalty, which is what "location first"
# actually means in this arithmetic.

WEIGHTED_AXES: tuple[str, ...] = (
    "hard_requirements",
    "core_stack",
    "years",
    "level",
    "nice_to_haves",
)

# The 4 named presets. "balanced" is empty on purpose: it means "use whatever
# table the method that ran shipped with", so a user who never opens this
# control sees exactly the numbers they saw before it existed (assumption A7).
PRESETS: Mapping[str, Mapping[str, float]] = {
    "balanced": {},
    "location_first": {},
    "seniority_first": {
        "hard_requirements": 0.25,
        "core_stack": 0.25,
        "years": 0.10,
        "level": 0.35,
        "nice_to_haves": 0.05,
    },
    "skills_only": {
        "hard_requirements": 0.45,
        "core_stack": 0.55,
        "years": 0.0,
        "level": 0.0,
        "nice_to_haves": 0.0,
    },
}

# How hard a location the user did not ask for pushes a job down, per preset.
# `location_first` is the only preset that moves it, because the weighted axes
# cannot express "I care about location" at all.
PRESET_LOCATION_PENALTY: Mapping[str, int] = {
    "balanced": LOCATION_MISS_PENALTY,
    "location_first": 35,
    "seniority_first": LOCATION_MISS_PENALTY,
    "skills_only": 0,
}

# The table a method shipped with, for a job scored by that method. AI scoring
# is absent because it asks a model for the total rather than weighting axes.
METHOD_WEIGHTS: Mapping[str, Mapping[str, float]] = {
    "jev": JEV_WEIGHTS,
    "keyword": KEYWORD_WEIGHTS,
}


def user_total(
    factors: Sequence[Factor],
    weights: Mapping[str, float],
    *,
    location_penalty: int = LOCATION_MISS_PENALTY,
) -> int | None:
    """The score these factors deserve under the user's own weights.

    `None` when the factors can't answer: no graded axis the table names is
    present, so there is nothing to weight and the stored score stands. That is
    the honest outcome for a job whose method recorded no factors, and it is why
    this returns an option rather than a 0.
    """
    by_key = {f.factor: f for f in factors}
    present = {
        key: w
        for key, w in weights.items()
        if key in by_key and by_key[key].kind != "probability" and w > 0
    }
    if not present:
        return None
    scale = sum(present.values())
    total = sum(by_key[key].value * w for key, w in present.items()) / scale * 100
    location = by_key.get("location_preference")
    if location is not None and location.value < 0.5:
        total -= location_penalty
    return max(0, min(100, round(total)))
