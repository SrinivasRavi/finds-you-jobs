"""Priority z-band assignment via Welford's online algorithm (FR-TR-09 / US-TR-10).

The system keeps a running mean (μ) and variance (via M2) over the match scores
of *all jobs ever scored* — three O(1) accumulators (`count / mean / m2`), no
scores stored. At Save, an application's priority is the z-band of its score at
the normal-quartile cut-points (±0.674σ). A card saved while its score is still
`Pending` skips the z-band and is stamped `P0` (the strongest priority signal —
the user kept it regardless of the number). Priority is set once at Save, stored
on the `Application`, and never auto-recalculated; a manual override always wins.

The accumulators live in `UserPreferences.thresholds["score_stats"]` (no schema
change): the scorer feeds `welford_update` when a *new* score is cached; Save
reads the snapshot and calls `zband_priority`.
"""

from __future__ import annotations

from typing import Any

# Normal-distribution quartile cut-point (±0.674σ splits the mass into 4 bands).
_Z_CUT = 0.674
# Below this many scored jobs σ is unstable → everything defaults to P2 (cold start).
_COLD_START_COUNT = 20

# Where the running accumulators live inside UserPreferences.thresholds (JSON).
STATS_KEY = "score_stats"


def empty_stats() -> dict[str, Any]:
    """A fresh Welford accumulator (no scores seen yet)."""
    return {"count": 0, "mean": 0.0, "m2": 0.0}


def welford_update(stats: dict[str, Any] | None, score: float) -> dict[str, Any]:
    """Fold one new score into the running (count, mean, M2) — O(1), no history.

    Returns a *new* dict (callers persist it back onto prefs.thresholds)."""
    count = int((stats or {}).get("count", 0)) + 1
    mean = float((stats or {}).get("mean", 0.0))
    m2 = float((stats or {}).get("m2", 0.0))
    delta = score - mean
    mean += delta / count
    m2 += delta * (score - mean)
    return {"count": count, "mean": mean, "m2": m2}


def zband_priority(stats: dict[str, Any] | None, score: float) -> str:
    """The P0–P3 band of `score` against the running distribution (FR-TR-09).

    `P0: z ≥ +0.674 · P1: 0 ≤ z < +0.674 · P2: −0.674 ≤ z < 0 · P3: z < −0.674`.
    Cold start (`count < 20`) or a degenerate σ (all scores identical) → `P2`."""
    stats = stats or {}
    count = int(stats.get("count", 0))
    if count < _COLD_START_COUNT:
        return "P2"
    variance = float(stats.get("m2", 0.0)) / count  # population variance
    std = variance**0.5
    if std <= 0:
        return "P2"
    z = (score - float(stats.get("mean", 0.0))) / std
    if z >= _Z_CUT:
        return "P0"
    if z >= 0:
        return "P1"
    if z >= -_Z_CUT:
        return "P2"
    return "P3"
