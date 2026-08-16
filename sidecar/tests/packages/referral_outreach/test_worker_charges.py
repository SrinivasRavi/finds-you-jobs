# voyager_py/tests/test_worker_charges.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Charge-on-attempt, refunds, and the plan-aware notes budget in the worker
send paths (posture doc section 4 fixes 4 + 10, section 6).

The ledger must drift in the SAFE direction: an unproven send stays charged
(it may have reached LinkedIn), while a PROVEN no-send — LinkedIn's weekly-cap
dialog, a definite DM no-thread miss — refunds its attempt charge. The
personalized-note budget binds only on free-plan accounts, and a note dropped
by LinkedIn's own upsell marks the allowance observed-exhausted.
"""

from __future__ import annotations

import pytest

from sidecar.packages.referral_outreach.upstream import actions, pacing, session, worker
from sidecar.packages.referral_outreach.upstream.errors import (
    RateLimited,
    ReachedConnectionLimit,
)
from sidecar.packages.referral_outreach.upstream.pacing import Pacer, resolve_profile


class _FakeSession:
    def __init__(self, **kwargs):  # noqa: D401 — mirrors AccountSession's ctor
        pass

    def run_browser(self, action):
        # The action-on-the-lane bridge (broker-backed) / inline self-launch:
        # the fake just runs the (monkeypatched) action inline, like the legacy
        # self-launch path does when no broker surface is provided.
        return action()

    def close(self) -> None:
        pass


@pytest.fixture
def no_jitter(monkeypatch):
    """Zero the inter-send gap so multi-send tests never sleep 30-90 s."""
    monkeypatch.setattr(pacing, "send_delay_seconds", lambda: 0.0)


@pytest.fixture
def fake_browser(monkeypatch):
    monkeypatch.setattr(session, "AccountSession", _FakeSession)


def _invites_used(state_dir) -> int:
    return Pacer(resolve_profile(None), state_dir=state_dir).remaining()["daily_used"]


def _notes_used(state_dir) -> int:
    p = Pacer(resolve_profile(None), state_dir=state_dir)
    return p.usage("notes")["month_used"]


# ── invites: charge on attempt, refund on proven no-send ─────────────────────


def test_unproven_send_stays_charged(tmp_path, fake_browser, no_jitter, monkeypatch):
    """A crash AFTER the click may still have sent the invite — keep the charge
    (drifting low is the unsafe direction)."""
    def _boom(sess, pid, note="", on_step=None):
        raise RuntimeError("browser died mid-verification")

    monkeypatch.setattr(actions, "send_connection_request", _boom)
    with pytest.raises(RuntimeError):
        worker.send_connection("someone", state_dir=str(tmp_path))
    assert _invites_used(tmp_path) == 1


def test_weekly_limit_dialog_refunds_the_attempt(tmp_path, fake_browser, no_jitter, monkeypatch):
    """LinkedIn's weekly-cap dialog appears INSTEAD of the invite sending — a
    proven no-send: refund, then enter backoff."""
    def _blocked(sess, pid, note="", on_step=None):
        raise ReachedConnectionLimit("Weekly connection limit pop up appeared")

    monkeypatch.setattr(actions, "send_connection_request", _blocked)
    out = worker.send_connection("someone", state_dir=str(tmp_path))
    assert out["error"] == "rate_limited"
    assert _invites_used(tmp_path) == 0
    assert Pacer(resolve_profile(None), state_dir=tmp_path).is_paused()


def test_successful_send_is_charged_once(tmp_path, fake_browser, no_jitter, monkeypatch):
    monkeypatch.setattr(
        actions, "send_connection_request", lambda s, p, note="", on_step=None: ("pending", "")
    )
    out = worker.send_connection("someone", state_dir=str(tmp_path))
    assert out["ok"] and out["sent"]
    assert _invites_used(tmp_path) == 1


# ── notes: plan-aware budget + observed exhaustion ───────────────────────────


def test_free_plan_charges_the_note_and_gates_at_the_cap(
    tmp_path, fake_browser, no_jitter, monkeypatch
):
    monkeypatch.setattr(
        actions, "send_connection_request",
        lambda s, p, note="", on_step=None: ("pending", "with_note"),
    )
    for i in range(3):  # notes cap: 3 / rolling 30 d
        out = worker.send_connection(
            f"p{i}", note="hi", state_dir=str(tmp_path), linkedin_plan="free"
        )
        assert out["ok"], out
    assert _notes_used(tmp_path) == 3
    refused = worker.send_connection(
        "p4", note="hi", state_dir=str(tmp_path), linkedin_plan="free"
    )
    assert refused["error"] == "cap_or_backoff"
    assert "note" in refused["reason"]
    # The refused send never touched the invite budget either.
    assert _invites_used(tmp_path) == 3


def test_premium_plan_never_gates_on_notes(tmp_path, fake_browser, no_jitter, monkeypatch):
    monkeypatch.setattr(
        actions, "send_connection_request",
        lambda s, p, note="", on_step=None: ("pending", "with_note"),
    )
    for i in range(5):
        out = worker.send_connection(
            f"p{i}", note="hi", state_dir=str(tmp_path), linkedin_plan="premium"
        )
        assert out["ok"], out
    assert _notes_used(tmp_path) == 0  # premium: not charged at all


def test_upsell_degrade_saturates_the_note_allowance(
    tmp_path, fake_browser, no_jitter, monkeypatch
):
    """LinkedIn's Premium upsell = ground truth that the free allowance is out:
    the dropped note is refunded, the meter is marked exhausted, and the next
    note-bearing send is refused up front instead of silently degrading."""
    monkeypatch.setattr(
        actions,
        "send_connection_request",
        lambda s, p, note="", on_step=None: ("pending", "noteless_upsell"),
    )
    out = worker.send_connection(
        "p1", note="hi", state_dir=str(tmp_path), linkedin_plan="free"
    )
    assert out["ok"] and out["note_outcome"] == "noteless_upsell"
    p = Pacer(resolve_profile(None), state_dir=tmp_path)
    assert p.usage("notes")["month_remaining"] == 0
    refused = worker.send_connection(
        "p2", note="hi", state_dir=str(tmp_path), linkedin_plan="free"
    )
    assert refused["error"] == "cap_or_backoff"


# ── DMs: charge on attempt, refund on proven no-send ─────────────────────────


def test_dm_proven_no_send_is_refunded(tmp_path, fake_browser, no_jitter, monkeypatch):
    monkeypatch.setattr(actions, "send_dm", lambda s, p, m, on_step=None: False)
    out = worker.send_dm("someone", "hello", state_dir=str(tmp_path))
    assert out["ok"] is False and out["sent"] is False
    assert Pacer(resolve_profile(None), state_dir=tmp_path).remaining()["dm_daily_sent"] == 0


def test_dm_rate_limit_refunds_and_backs_off(tmp_path, fake_browser, no_jitter, monkeypatch):
    def _throttled(s, p, m, on_step=None):
        raise RateLimited("LinkedIn returned HTTP 429 (throttled/blocked)")

    monkeypatch.setattr(actions, "send_dm", _throttled)
    out = worker.send_dm("someone", "hello", state_dir=str(tmp_path))
    assert out["error"] == "rate_limited"
    p = Pacer(resolve_profile(None), state_dir=tmp_path)
    assert p.remaining()["dm_daily_sent"] == 0
    assert p.is_paused()


def test_dm_unproven_send_stays_charged(tmp_path, fake_browser, no_jitter, monkeypatch):
    def _boom(s, p, m, on_step=None):
        raise RuntimeError("browser died mid-send")

    monkeypatch.setattr(actions, "send_dm", _boom)
    with pytest.raises(RuntimeError):
        worker.send_dm("someone", "hello", state_dir=str(tmp_path))
    assert Pacer(resolve_profile(None), state_dir=tmp_path).remaining()["dm_daily_sent"] == 1


# ── job search: the package-owned 25 ceiling ─────────────────────────────────


def test_search_jobs_is_always_one_page_of_25(tmp_path):
    out = worker.search_jobs(
        "python", state_dir=str(tmp_path), dry_run=True
    )
    assert "one page of 25" in out["plan"]


def test_search_jobs_dry_run_reports_offset(tmp_path):
    out = worker.search_jobs(
        "python", start=50, state_dir=str(tmp_path), dry_run=True
    )
    assert "offset 50" in out["plan"]


class _FakeSearchSession:
    def __init__(self, **kwargs):
        pass

    def ensure_browser(self) -> None:
        pass

    def run_browser(self, action):
        return action()

    def close(self) -> None:
        pass


def _fake_search_api(calls, total=60):
    """A PlaywrightLinkedinAPI stand-in serving `total` jobs in pages of `count`."""

    class _API:
        def __init__(self, sess):
            pass

        def search_jobs(self, keywords, location, *, start, count):
            calls.append((start, count))
            n = max(0, min(count, total - start))
            return {"total": total, "jobs": [
                {"id": str(start + i), "title": "t", "url": f"u{start + i}"}
                for i in range(n)
            ]}

    return _API


def test_search_jobs_start_offsets_the_single_page(tmp_path, monkeypatch):
    """`start` (the host's Next-page cursor) shifts the one-page window: the
    wire request carries the offset verbatim, the page size stays 25 (the
    per-call clamp is untouched), and `next_start` points at the next page."""
    from sidecar.packages.referral_outreach.upstream import client as client_mod

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(session, "AccountSession", _FakeSearchSession)
    monkeypatch.setattr(client_mod, "PlaywrightLinkedinAPI", _fake_search_api(calls))

    out = worker.search_jobs(
        "python", start=25, state_dir=str(tmp_path)
    )
    assert calls == [(25, 25)]  # one request: offset verbatim, count fixed
    assert out["start"] == 25
    assert out["next_start"] == 50
    assert out["exhausted"] is False  # 60 total → a page 3 exists
    assert out["count"] == 25


def test_search_jobs_reports_end_of_results(tmp_path, monkeypatch):
    """The last page (short page / total reached) surfaces `exhausted` so the
    host cursor can stop offering Next page without wasting a request."""
    from sidecar.packages.referral_outreach.upstream import client as client_mod

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(session, "AccountSession", _FakeSearchSession)
    monkeypatch.setattr(client_mod, "PlaywrightLinkedinAPI", _fake_search_api(calls))

    out = worker.search_jobs(
        "python", start=50, state_dir=str(tmp_path)
    )
    assert calls == [(50, 25)]
    assert out["count"] == 10  # 60 total — only 10 left at offset 50
    assert out["exhausted"] is True


def test_search_jobs_charges_and_throttles_pages_per_hour(tmp_path, monkeypatch):
    """The self-imposed pages/hour throttle: each search charges one page against
    the shared hourly ledger, and once the budget is spent search_jobs refuses
    BEFORE issuing another request (no LinkedIn traffic for a blocked call)."""
    from sidecar.packages.referral_outreach.upstream import client as client_mod
    from sidecar.packages.referral_outreach.upstream.pacing import PacingProfile

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(session, "AccountSession", _FakeSearchSession)
    monkeypatch.setattr(client_mod, "PlaywrightLinkedinAPI", _fake_search_api(calls))

    # Pin the hourly ceiling to 2 pages so the throttle is quick to reach.
    profile = PacingProfile(overrides={"job_search_pages_hour": 2})
    for _ in range(2):
        out = worker.search_jobs("python", profile=profile, state_dir=str(tmp_path))
        assert out["ok"] is True
    # Third call is refused with no new LinkedIn request.
    before = len(calls)
    out = worker.search_jobs("python", profile=profile, state_dir=str(tmp_path))
    assert out["ok"] is False
    assert out["error"] == "rate_limited_hourly"
    assert len(calls) == before  # no request issued for the blocked call


def test_search_jobs_enters_backoff_on_throttle(tmp_path, monkeypatch):
    """A LinkedIn throttle (429/999/503 → RateLimited) DURING job search must
    enter the 24 h backoff — like `discover` — so later pairs and later searches
    stop. search_jobs used to let RateLimited propagate uncaught, entering no
    backoff, and the loudest read path kept hammering after LinkedIn said stop."""
    from sidecar.packages.referral_outreach.upstream import client as client_mod

    class _ThrottlingAPI:
        def __init__(self, sess):
            pass

        def search_jobs(self, keywords, location, *, start, count):
            raise RateLimited("LinkedIn returned HTTP 429 (throttled/blocked)")

    from sidecar.packages.referral_outreach.upstream.pacing import (
        PacingProfile,
        resolve_profile,
    )

    monkeypatch.setattr(session, "AccountSession", _FakeSearchSession)
    monkeypatch.setattr(client_mod, "PlaywrightLinkedinAPI", _ThrottlingAPI)

    profile = PacingProfile()  # free · 60% → a real job_search_pages hourly ceiling
    out = worker.search_jobs("python", profile=profile, state_dir=str(tmp_path))
    assert out["ok"] is False
    assert out["error"] == "rate_limited"
    # Backoff is now persisted — a fresh pacer reads the pause.
    p = Pacer(resolve_profile(profile), state_dir=tmp_path)
    assert p.is_paused()
    # The attempted page (which reached LinkedIn and 429'd) is still charged —
    # charge-on-attempt keeps the hourly ledger from drifting low.
    assert p.usage("job_search_pages")["hour_used"] == 1


def test_send_narrates_steps_to_the_host(tmp_path, fake_browser, no_jitter, monkeypatch):
    """The 2026-08-16 narration fork: the worker reports the pacing wait as
    step 1 (`invite1`/`dm1`) and hands the SAME callback to the driving action,
    so every later step reaches the host from the code that performed it."""
    seen: list[str] = []

    def _drive(s, p, note="", on_step=None):
        assert on_step is not None
        on_step("invite2")
        return ("pending", "")

    monkeypatch.setattr(actions, "send_connection_request", _drive)
    out = worker.send_connection(
        "someone", note="", state_dir=str(tmp_path), on_step=seen.append
    )
    assert out["sent"] is True
    assert seen == ["invite1", "invite2"]

    seen.clear()

    def _dm_drive(s, p, m, on_step=None):
        assert on_step is not None
        on_step("dm2")
        return True

    monkeypatch.setattr(actions, "send_dm", _dm_drive)
    out = worker.send_dm(
        "someone", "hello", state_dir=str(tmp_path), on_step=seen.append
    )
    assert out["sent"] is True
    assert seen == ["dm1", "dm2"]


def test_step_narration_failure_never_breaks_the_send(
    tmp_path, fake_browser, no_jitter, monkeypatch
):
    """A callback that explodes must not fail the send it narrates."""
    def _bad_callback(step: str) -> None:
        raise RuntimeError("listener died")

    monkeypatch.setattr(
        actions, "send_connection_request",
        lambda s, p, note="", on_step=None: ("pending", ""),
    )
    out = worker.send_connection(
        "someone", note="", state_dir=str(tmp_path), on_step=_bad_callback
    )
    assert out["sent"] is True
