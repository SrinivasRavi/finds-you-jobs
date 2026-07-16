"""Covers: FR-TR-09 / US-TR-10 priority z-band via Welford.

Pure-function tests of the running accumulator + the z-band bucketing. The
Save-time stamping (pending → P0, override-wins) is exercised over HTTP in
test_applications_vertical.py; here we pin the math.
"""

from __future__ import annotations

import statistics

import pytest

from sidecar.app.priority import empty_stats, welford_update, zband_priority


def _fold(scores: list[float]) -> dict:
    stats = empty_stats()
    for s in scores:
        stats = welford_update(stats, s)
    return stats


def test_welford_matches_batch_mean_and_variance() -> None:
    scores = [70.0, 55.0, 82.0, 91.0, 63.0, 48.0, 77.0]
    stats = _fold(scores)
    assert stats["count"] == len(scores)
    assert stats["mean"] == pytest.approx(statistics.fmean(scores))
    # M2 / count == population variance.
    assert (stats["m2"] / stats["count"]) == pytest.approx(statistics.pvariance(scores))


def test_cold_start_defaults_everything_to_p2() -> None:
    # Fewer than 20 scores → σ is unstable → P2 regardless of the score.
    stats = _fold([10.0] * 19 + [99.0])  # 20 total after the last fold
    # 19 scored: still cold.
    cold = _fold([10.0] * 19)
    assert cold["count"] == 19
    assert zband_priority(cold, 99.0) == "P2"
    assert zband_priority(cold, 1.0) == "P2"
    # 20th crosses the threshold → real bucketing kicks in.
    assert stats["count"] == 20


def test_zband_buckets_at_quartile_cut_points() -> None:
    # 40 scores centered at 60, σ≈10 → z-bands land on the ±0.674 cut points.
    scores = [60.0 + 10.0 * ((i % 20) - 10) / 6.0 for i in range(40)]
    stats = _fold(scores)
    mean = stats["mean"]
    std = (stats["m2"] / stats["count"]) ** 0.5
    assert zband_priority(stats, mean + 1.0 * std) == "P0"  # z=+1 ≥ +0.674
    assert zband_priority(stats, mean + 0.3 * std) == "P1"  # 0 ≤ z < +0.674
    assert zband_priority(stats, mean - 0.3 * std) == "P2"  # −0.674 ≤ z < 0
    assert zband_priority(stats, mean - 1.0 * std) == "P3"  # z < −0.674


def test_recentering_still_yields_p0_when_all_scores_cluster() -> None:
    # The "max score is 70 → no P0" problem: bands re-center so P0 still exists.
    scores = [68.0, 69.0, 70.0, 71.0, 72.0] * 5  # 25 scores, all near 70
    stats = _fold(scores)
    assert stats["count"] >= 20
    assert zband_priority(stats, 72.0) == "P0"  # top of the cluster is still P0


def test_degenerate_sigma_falls_back_to_p2() -> None:
    stats = _fold([50.0] * 25)  # zero variance
    assert zband_priority(stats, 50.0) == "P2"


def test_update_returns_new_dict_and_does_not_mutate() -> None:
    stats = empty_stats()
    out = welford_update(stats, 80.0)
    assert stats == {"count": 0, "mean": 0.0, "m2": 0.0}  # original untouched
    assert out["count"] == 1 and out["mean"] == 80.0
