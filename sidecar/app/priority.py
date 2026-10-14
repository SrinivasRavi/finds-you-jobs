"""Priority z-bands via Welford's online algorithm (FR-TR-09 / US-TR-10).

A running mean and variance over every score ever made, in 3 accumulators in
`thresholds["score_stats"]`. At Save a card's priority is its score's z-band at
±0.674σ; a card saved while pending gets P0. Set once at Save; a manual
override always wins."""

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
