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
