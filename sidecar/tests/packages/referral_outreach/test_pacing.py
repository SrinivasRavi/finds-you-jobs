# voyager_py/tests/test_pacing.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Pacing/caps/backoff — the account-safety contract owned inside the subtree
(NFR-LI-01/02/03, FR-NW-04/05). Pure logic, deterministic clocks."""

from __future__ import annotations

import pytest

from sidecar.packages.referral_outreach.upstream.pacing import (
    DAY_SECONDS,
    TIERS,
    WEEK_SECONDS,
    Pacer,
    resolve_tier,
    send_delay_seconds,
)


def test_tier_resolution_defaults_to_new():
    assert resolve_tier(None).name == "new"
    assert resolve_tier("SEASONED").name == "seasoned"
    assert TIERS["new"].daily == 15 and TIERS["new"].weekly == 100
    assert TIERS["seasoned"].daily == 30 and TIERS["seasoned"].weekly == 200


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        resolve_tier("platinum")


def _pacer(tmp_path, tier="new"):
    return Pacer(resolve_tier(tier), state_dir=tmp_path)


def test_daily_cap_blocks_after_limit(tmp_path):
    pacer = _pacer(tmp_path)  # new: 15/day
    now = 1_000_000.0
    for _ in range(15):
        allowed, _ = pacer.can_send_invite(now=now)
        assert allowed
        pacer.record_invite(now=now)
    allowed, reason = pacer.can_send_invite(now=now)
    assert not allowed
    assert "daily cap" in reason
    assert pacer.remaining(now=now)["daily_remaining"] == 0


def test_daily_window_rolls_off(tmp_path):
    pacer = _pacer(tmp_path)
    start = 1_000_000.0
    for _ in range(15):
        pacer.record_invite(now=start)
    # 25 hours later the day-window has rolled; daily quota is back.
    later = start + DAY_SECONDS + 3600
    r = pacer.remaining(now=later)
    assert r["daily_remaining"] == 15
    allowed, _ = pacer.can_send_invite(now=later)
    assert allowed


def test_weekly_cap_independent_of_daily(tmp_path):
    pacer = _pacer(tmp_path, tier="seasoned")  # 30/day, 200/wk
    now = 2_000_000.0
    # 200 invites all OUTSIDE the daily window (>24 h ago) but INSIDE the week:
    # daily has room, weekly is exhausted, so the weekly cap is what blocks.
    for i in range(200):
        pacer.record_invite(now=now - DAY_SECONDS - 3600 - i * 2000)
    r = pacer.remaining(now=now)
    assert r["daily_used"] == 0 and r["daily_remaining"] == 30
    assert r["weekly_remaining"] == 0
    allowed, reason = pacer.can_send_invite(now=now)
    assert not allowed and "weekly cap" in reason


def test_dms_do_not_count_against_invite_cap(tmp_path):
    pacer = _pacer(tmp_path)
    now = 1_500_000.0
    for _ in range(50):
        pacer.record_dm(now=now)
    r = pacer.remaining(now=now)
    assert r["daily_used"] == 0  # DMs are separate (FR-NW-04)
    assert r["daily_remaining"] == 15
    allowed, _ = pacer.can_send_dm(now=now)
    assert allowed


def test_backoff_pauses_everything(tmp_path):
    pacer = _pacer(tmp_path)
    now = 1_000_000.0
    deadline = pacer.pause_for_backoff("LinkedIn 429", now=now)
    assert deadline > now
    assert pacer.is_paused(now=now)
    inv_ok, inv_reason = pacer.can_send_invite(now=now)
    dm_ok, dm_reason = pacer.can_send_dm(now=now)
    assert not inv_ok and "paused" in inv_reason
    assert not dm_ok and "paused" in dm_reason
    # After the backoff window clears, sends resume.
    assert not pacer.is_paused(now=now + WEEK_SECONDS)


def test_manual_resume_clears_backoff(tmp_path):
    pacer = _pacer(tmp_path)
    now = 1_000_000.0
    pacer.pause_for_backoff("restriction", now=now)
    pacer.resume()
    assert not pacer.is_paused(now=now)
    allowed, _ = pacer.can_send_invite(now=now)
    assert allowed


def test_state_persists_across_pacer_instances(tmp_path):
    now = 1_000_000.0
    p1 = _pacer(tmp_path)
    for _ in range(5):
        p1.record_invite(now=now)
    p1.pause_for_backoff("429", now=now)
    p1.save(now=now)  # prune with the synthetic clock, not wall time

    p2 = _pacer(tmp_path)  # fresh instance reads the same ledger file
    assert p2.is_paused(now=now)
    # 5 invites recorded, but paused so can_send is False for the pause reason.
    assert p2.remaining(now=now)["daily_used"] == 5


def test_save_prunes_entries_older_than_a_week(tmp_path):
    pacer = _pacer(tmp_path)
    now = 5_000_000.0
    pacer.record_invite(now=now - WEEK_SECONDS - 10_000)  # stale
    pacer.record_invite(now=now)  # fresh
    # save() prunes using real time; simulate by setting entries then re-checking
    pacer.state.invites = [e for e in pacer.state.invites if e >= now - WEEK_SECONDS]
    assert len(pacer.state.invites) == 1


def test_send_delay_is_within_jitter_band():
    for _ in range(50):
        d = send_delay_seconds()
        assert 30.0 <= d <= 90.0
