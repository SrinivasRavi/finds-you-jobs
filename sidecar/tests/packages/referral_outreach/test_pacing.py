# voyager_py/tests/test_pacing.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Pacing/caps/backoff — the account-safety contract owned inside the subtree
(NFR-LI-01/02/03, FR-NW-04/05). Pure logic, deterministic clocks."""

from __future__ import annotations

import pytest

from sidecar.packages.referral_outreach.upstream.pacing import (
    DAY_SECONDS,
    DEFAULT_RISK_PCT,
    HOUR_SECONDS,
    TIERS,
    WEEK_SECONDS,
    Pacer,
    PacingProfile,
    default_overrides,
    resolve_profile,
    resolve_tier,
    send_delay_seconds,
)


def test_tier_resolution_defaults_to_new():
    assert resolve_tier(None).name == "new"
    assert resolve_tier("SEASONED").name == "seasoned"
    # 50-70% of the ESTIMATED LinkedIn ceiling (posture doc §4). These were
    # 15/100 and 30/200 — i.e. `seasoned` weekly sat at ~200% of the ~100/wk
    # soft cap, so the app was a foot-gun rather than a guard.
    assert TIERS["new"].daily == 8 and TIERS["new"].weekly == 30
    assert TIERS["seasoned"].daily == 10 and TIERS["seasoned"].weekly == 65
    assert resolve_tier("recovering").name == "recovering"
    assert TIERS["recovering"].daily == 3 and TIERS["recovering"].weekly == 15


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        resolve_tier("platinum")


# --- membership × risk% × override (maintainer directive 2026-08-01) ---------


def test_default_profile_reproduces_todays_new_caps():
    """The whole calibration: free membership × the default 60% risk lands on
    the historical New-tier caps exactly, so nothing shipped changed."""
    assert DEFAULT_RISK_PCT == 60
    t = resolve_profile(PacingProfile())  # free · 60% · no overrides
    assert t.invites.day == 8 and t.invites.week == 30
    assert t.dms.day == 10 and t.dms.week == 50
    assert t.profile_views.day == 25
    assert t.searches.month == 150
    assert t.notes.month == 3
    assert t.job_search_pages.hour == 4  # 7 × 0.60 → 4


def test_risk_100_sits_at_the_ceiling():
    t = resolve_profile(PacingProfile(risk_pct=100))
    assert t.invites.week == 50  # the estimated ceiling itself
    assert t.job_search_pages.hour == 7


def test_risk_clamps_and_never_zeroes_a_live_meter():
    # risk floors at 10%; scaled caps floor at 1 so a meter never becomes 0/unusable.
    t = resolve_profile(PacingProfile(risk_pct=1))  # clamped to 10
    assert (t.notes.month or 0) >= 1  # 5 × 0.10 = 0.5 → floored to 1, not 0


def test_override_pins_a_single_meter_window():
    t = resolve_profile(PacingProfile(overrides={"invites_week": 42}))
    assert t.invites.week == 42          # pinned
    assert t.invites.day == 8            # untouched, still scaled
    assert t.dms.week == 50              # other meters untouched


def test_override_is_clamped_to_the_ceiling():
    """An override can never exceed our estimate of LinkedIn's own limit — the UI
    dropdown offers only in-range values, and the package clamps as the backstop
    so the 'never above the estimated max' guarantee holds whatever is stored."""
    t = resolve_profile(PacingProfile(overrides={"invites_week": 9999}))
    assert t.invites.week == 50          # free ceiling, not 9999
    t = resolve_profile(PacingProfile(membership="free", overrides={"searches_month": 10_000}))
    assert t.searches.month == 250       # free searches ceiling


def test_premium_lifts_the_paid_ceilings():
    free = resolve_profile(PacingProfile(membership="free", risk_pct=100))
    prem = resolve_profile(PacingProfile(membership="premium", risk_pct=100))
    assert (prem.dms.week or 0) > (free.dms.week or 0)
    assert (prem.searches.month or 0) > (free.searches.month or 0)


def test_default_overrides_matches_the_scaled_caps():
    """`default_overrides` (what the UI resets pins TO) equals the scaled caps."""
    d = default_overrides("free", 60)
    assert d["invites_week"] == 30
    assert d["job_search_pages_hour"] == 4


def test_job_search_pages_meter_is_hourly():
    p = Pacer(resolve_profile(PacingProfile()), state_dir=None)
    now = 1_000_000.0
    for _ in range(4):
        p.record_search_page(now=now)
    allowed, reason = p.can_search_jobs(now=now)
    assert not allowed and "job_search_pages" in reason
    # An hour later the rolling window has cleared.
    assert p.can_search_jobs(now=now + HOUR_SECONDS + 1)[0]


def _pacer(tmp_path, tier="new"):
    return Pacer(resolve_tier(tier), state_dir=tmp_path)


def test_daily_cap_blocks_after_limit(tmp_path):
    pacer = _pacer(tmp_path)  # new: 8/day
    now = 1_000_000.0
    for _ in range(8):
        allowed, _ = pacer.can_send_invite(now=now)
        assert allowed
        pacer.record_invite(now=now)
    allowed, reason = pacer.can_send_invite(now=now)
    assert not allowed
    assert "invites cap reached" in reason and "/day" in reason
    assert pacer.remaining(now=now)["daily_remaining"] == 0


def test_daily_window_rolls_off(tmp_path):
    pacer = _pacer(tmp_path)
    start = 1_000_000.0
    for _ in range(8):
        pacer.record_invite(now=start)
    # 25 hours later the day-window has rolled; daily quota is back.
    later = start + DAY_SECONDS + 3600
    r = pacer.remaining(now=later)
    assert r["daily_remaining"] == 8
    allowed, _ = pacer.can_send_invite(now=later)
    assert allowed


def test_weekly_cap_independent_of_daily(tmp_path):
    pacer = _pacer(tmp_path, tier="seasoned")  # 10/day, 65/wk
    now = 2_000_000.0
    # 65 invites all OUTSIDE the daily window (>24 h ago) but INSIDE the week:
    # daily has room, weekly is exhausted, so the weekly cap is what blocks.
    for i in range(65):
        pacer.record_invite(now=now - DAY_SECONDS - 3600 - i * 2000)
    r = pacer.remaining(now=now)
    assert r["daily_used"] == 0 and r["daily_remaining"] == 10
    assert r["weekly_remaining"] == 0
    allowed, reason = pacer.can_send_invite(now=now)
    assert not allowed and "invites cap reached" in reason and "/week" in reason


def test_dms_do_not_count_against_invite_cap(tmp_path):
    """DMs still have their OWN ledger and never decrement invites (FR-NW-04) —
    but they are no longer uncapped."""
    pacer = _pacer(tmp_path)
    now = 1_500_000.0
    for _ in range(5):
        pacer.record_dm(now=now)
    r = pacer.remaining(now=now)
    assert r["daily_used"] == 0  # DMs are separate (FR-NW-04)
    assert r["daily_remaining"] == 8
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


# --- inter-send spacing (NFR-LI-01) -----------------------------------------
# Regression cover for the defect where the 30-90 s jitter was only ever
# *reported* as `delay_hint_s` and nothing slept on it, so a batch drained the
# daily cap at machine pace. See `docs/internal/linkedin-posture.md` §1.


def test_first_send_of_account_life_never_waits(tmp_path):
    pacer = _pacer(tmp_path)
    assert pacer.last_send_at() == 0.0
    assert pacer.seconds_until_next_send(now=1_000_000.0) == 0.0


def test_wait_is_required_immediately_after_a_send(tmp_path):
    pacer = _pacer(tmp_path)
    now = 1_000_000.0
    pacer.record_invite(now=now)
    wait = pacer.seconds_until_next_send(now=now)
    # Jitter band is 30-90 s, so straight after a send the wait is within it.
    assert 30.0 <= wait <= 90.0


def test_wait_decays_and_reaches_zero_past_the_band(tmp_path):
    pacer = _pacer(tmp_path)
    now = 1_000_000.0
    pacer.record_invite(now=now)
    # Past the widest possible gap, no wait remains however the jitter falls.
    assert pacer.seconds_until_next_send(now=now + 91.0) == 0.0


def test_dms_and_invites_share_one_send_clock(tmp_path):
    """LinkedIn sees one account: a DM must space the next invite and vice
    versa, even though only invites decrement a cap (FR-NW-04)."""
    pacer = _pacer(tmp_path)
    now = 1_000_000.0
    pacer.record_dm(now=now)
    assert pacer.last_send_at() == now
    assert pacer.seconds_until_next_send(now=now) > 0.0


def test_spacing_survives_a_reload_so_batched_ops_cannot_bypass_it(tmp_path):
    """A batch is dispatched as N separate one-shot `send` ops, each building a
    fresh Pacer — so the gap has to come off the persisted ledger, not memory."""
    first = _pacer(tmp_path)
    now = 1_000_000.0
    first.record_invite(now=now)
    first.save(now=now)

    second = Pacer(resolve_tier("new"), state_dir=tmp_path)
    assert second.last_send_at() == pytest.approx(now)
    assert second.seconds_until_next_send(now=now) > 0.0


def test_wait_before_send_sleeps_the_computed_gap(tmp_path):
    pacer = _pacer(tmp_path)
    now = 1_000_000.0
    pacer.record_invite(now=now)
    slept: list[float] = []
    waited = pacer.wait_before_send(now=now, sleep=slept.append)
    assert slept and slept[0] == waited
    assert 30.0 <= waited <= 90.0


def test_wait_before_send_does_not_sleep_when_no_gap_is_owed(tmp_path):
    pacer = _pacer(tmp_path)
    slept: list[float] = []
    assert pacer.wait_before_send(now=1_000_000.0, sleep=slept.append) == 0.0
    assert slept == []


# --- reads are metered and backoff-gated (2026-07-30) -----------------------
# Before this, only sends built a Pacer. After a rate-limit signal the app kept
# running People searches, profile enrichment and contact-sync probes — the
# reads the restriction ladder actually watches. See posture doc §1.


def test_backoff_blocks_reads_not_just_sends(tmp_path):
    pacer = _pacer(tmp_path)
    now = 1_000_000.0
    pacer.pause_for_backoff("LinkedIn 999", now=now)
    for meter in ("invites", "dms", "profile_views", "searches", "notes"):
        allowed, reason = pacer.check(meter, now=now)
        assert not allowed, f"{meter} should be blocked during backoff"
        assert "paused" in reason


def test_profile_views_are_capped_daily(tmp_path):
    pacer = _pacer(tmp_path)  # new: 25 profile views/day
    now = 1_000_000.0
    pacer.record_profile_view(now=now, count=25)
    allowed, reason = pacer.can_view_profile(now=now)
    assert not allowed and "profile_views cap reached" in reason
    # Rolls off with the day window.
    assert pacer.can_view_profile(now=now + DAY_SECONDS + 60)[0]


def test_cul_searches_are_capped_monthly(tmp_path):
    pacer = _pacer(tmp_path)  # 150 CUL-counted searches/month
    now = 1_000_000.0
    for _ in range(150):
        pacer.record_search(now=now)
    allowed, reason = pacer.can_search(now=now)
    assert not allowed and "searches cap reached" in reason
    # A week later it is still blocked — this is a monthly window, and a flat
    # weekly prune used to be the bug that would have silently reset it.
    assert not pacer.can_search(now=now + WEEK_SECONDS)[0]


def test_dms_are_capped_and_no_longer_unlimited(tmp_path):
    pacer = _pacer(tmp_path)  # new: 10 DMs/day, 50/week
    now = 1_000_000.0
    for _ in range(10):
        assert pacer.can_send_dm(now=now)[0]
        pacer.record_dm(now=now)
    allowed, reason = pacer.can_send_dm(now=now)
    assert not allowed and "dms cap reached" in reason


def test_free_plan_note_budget_is_monthly(tmp_path):
    pacer = _pacer(tmp_path)  # 3 personalized notes / 30 d
    now = 1_000_000.0
    for _ in range(3):
        assert pacer.can_use_note(now=now)[0]
        pacer.record_note(now=now)
    assert not pacer.can_use_note(now=now)[0]


def test_per_meter_pruning_preserves_the_long_windows(tmp_path):
    """A flat one-week prune would have erased the note and CUL ledgers."""
    pacer = _pacer(tmp_path)
    now = 1_000_000.0
    ten_days_ago = now - 10 * DAY_SECONDS
    pacer.record_note(now=ten_days_ago)
    pacer.record_search(now=ten_days_ago)
    pacer.record_invite(now=ten_days_ago)
    pacer.save(now=now)

    reloaded = Pacer(resolve_tier("new"), state_dir=tmp_path)
    # Notes (30 d) and searches (31 d) survive; the invite (7 d) is pruned.
    assert len(reloaded.state.notes) == 1
    assert len(reloaded.state.searches) == 1
    assert reloaded.state.invites == []


def test_ledger_from_before_the_new_meters_still_loads(tmp_path):
    """An install upgrading in place must keep its invite history."""
    import json as _json

    (tmp_path / Pacer.STATE_FILENAME).write_text(
        _json.dumps({"invites": [1.0, 2.0], "dms": [3.0], "paused_until": 0.0})
    )
    pacer = Pacer(resolve_tier("new"), state_dir=tmp_path)
    assert pacer.state.invites == [1.0, 2.0]
    assert pacer.state.dms == [3.0]
    assert pacer.state.profile_views == [] and pacer.state.searches == []


# ── concurrency: merge-on-save, refunds, observed exhaustion ─────────────────
# Reads are metered now, and send / discover / contact-sync / job-search run in
# SEPARATE runner concurrency groups — so two Pacers can hold the ledger at
# once. save() must merge, not overwrite (posture doc §4 fix 6).


def test_concurrent_saves_merge_instead_of_clobbering(tmp_path):
    now = 1_000_000.0
    a = _pacer(tmp_path)
    b = _pacer(tmp_path)  # loaded before A saves — the lost-update setup
    a.record_invite(now=now)
    a.save(now=now)
    b.record_profile_view(now=now)
    b.save(now=now)  # used to write B's stale view and erase A's invite

    merged = Pacer(resolve_tier("new"), state_dir=tmp_path)
    assert len(merged.state.invites) == 1
    assert len(merged.state.profile_views) == 1


def test_save_is_atomic_no_tmp_file_left(tmp_path):
    pacer = _pacer(tmp_path)
    pacer.record_invite(now=1_000_000.0)
    pacer.save(now=1_000_000.0)
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_refund_gives_back_only_this_pacers_pending_charges(tmp_path):
    now = 1_000_000.0
    a = _pacer(tmp_path)
    a.record_invite(now=now)
    a.save(now=now)

    b = _pacer(tmp_path)  # sees A's invite as history, not as its own
    assert b.refund("invites") == 0  # must NOT strip another op's charge
    b.record_invite(now=now)
    assert b.refund("invites") == 1
    b.save(now=now)
    merged = Pacer(resolve_tier("new"), state_dir=tmp_path)
    assert len(merged.state.invites) == 1  # A's charge survived the refund


def test_pause_merges_by_max_and_resume_clears(tmp_path):
    now = 1_000_000.0
    a = _pacer(tmp_path)
    a.pause_for_backoff("throttled", now=now)
    a.save(now=now)

    # A resume issued by another pacer instance must beat the on-disk deadline —
    # otherwise the Settings Resume button silently does nothing.
    b = _pacer(tmp_path)
    b.resume()
    b.save(now=now)
    assert not Pacer(resolve_tier("new"), state_dir=tmp_path).is_paused(now=now)


def test_saturate_marks_a_meter_observed_exhausted(tmp_path):
    now = 1_000_000.0
    pacer = _pacer(tmp_path)
    pacer.record_note(now=now)
    pacer.saturate("notes", now=now)
    assert pacer.usage("notes", now=now)["month_remaining"] == 0
    assert not pacer.can_use_note(now=now)[0]
