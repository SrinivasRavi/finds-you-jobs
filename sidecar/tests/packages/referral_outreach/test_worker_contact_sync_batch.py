# voyager_py/tests/test_worker_contact_sync_batch.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""The one-session batched contact-status sweep (`worker.contact_sync_states`).

The host used to drive one `contact_sync` op per tracked contact, and every
browser op builds its own `_paced_session` — so a 5-contact sweep launched and
quit a full Chromium five times (~106 s live). The batch op must open ONE
session for N contacts while preserving the single op's per-contact semantics:
paced reads, charge-on-attempt profile views, 403/404 isolation,
first-429-stops-the-batch backoff, auth-stop-and-surface.

Zero live LinkedIn traffic — the session and the probe action are monkeypatched.
"""

from __future__ import annotations

import pytest

from sidecar.packages.referral_outreach.upstream import actions, session, worker
from sidecar.packages.referral_outreach.upstream.errors import (
    AuthenticationError,
    ProfileInaccessibleError,
    RateLimited,
)
from sidecar.packages.referral_outreach.upstream.pacing import Pacer, resolve_profile

_STATE = {
    "degree": 1, "is_first_degree": True,
    "last_message_direction": "me", "last_message_at": 1_700_000_000.0,
}


class _CountingSession:
    built = 0
    closed = 0

    def __init__(self, **kwargs):
        type(self).built += 1

    def close(self) -> None:
        type(self).closed += 1


@pytest.fixture
def one_browser(monkeypatch):
    _CountingSession.built = 0
    _CountingSession.closed = 0
    monkeypatch.setattr(session, "AccountSession", _CountingSession)
    return _CountingSession


@pytest.fixture
def sleeps(monkeypatch):
    """Capture (and skip) the inter-probe pacing sleeps so tests never wait."""
    recorded: list[tuple[float, float]] = []
    monkeypatch.setattr(
        session, "random_sleep", lambda lo, hi: recorded.append((lo, hi))
    )
    return recorded


def _views_used(state_dir) -> int:
    p = Pacer(resolve_profile(None), state_dir=state_dir)
    return p.usage("profile_views")["day_used"]


def test_batch_opens_one_session_for_n_contacts(
    tmp_path, one_browser, sleeps, monkeypatch
):
    probed: list[str] = []

    def _probe(sess, pid):
        probed.append(pid)
        return dict(_STATE)

    monkeypatch.setattr(actions, "get_contact_sync_state", _probe)
    out = worker.contact_sync_states(["a", "b", "c"], state_dir=str(tmp_path))
    assert out["ok"] is True and out["count"] == 3
    assert probed == ["a", "b", "c"]
    # THE point of the batch op: one browser for the whole sweep, torn down once.
    assert one_browser.built == 1
    assert one_browser.closed == 1
    # Per-contact semantics preserved: every probe charged, reads paced between
    # probes (N-1 inter-read pauses on top of the per-action page jitter).
    assert _views_used(tmp_path) == 3
    assert len(sleeps) == 2
    # Each entry is the single contact-sync envelope, in input order.
    assert [r["public_identifier"] for r in out["results"]] == ["a", "b", "c"]
    assert all(r["op"] == "contact-sync" and r["ok"] for r in out["results"])
    assert out["results"][0]["degree"] == 1


def test_rate_limited_backs_off_and_stops_the_sweep(
    tmp_path, one_browser, sleeps, monkeypatch
):
    """The first 429 stops the batch (§0.4): backoff is entered + persisted, the
    remaining contacts are never probed, and the attempted read stays charged
    (charge-on-attempt — it reached LinkedIn)."""
    probed: list[str] = []

    def _probe(sess, pid):
        probed.append(pid)
        if pid == "b":
            raise RateLimited("LinkedIn returned HTTP 429 (throttled/blocked)")
        return dict(_STATE)

    monkeypatch.setattr(actions, "get_contact_sync_state", _probe)
    out = worker.contact_sync_states(["a", "b", "c"], state_dir=str(tmp_path))
    assert probed == ["a", "b"]  # "c" never probed
    assert out["ok"] is False and out["error"] == "rate_limited"
    assert out["count"] == 2
    assert out["results"][1]["error"] == "rate_limited"
    assert out["results"][1]["paused_until"] > 0
    # Backoff persisted through the session's finally — a fresh pacer sees it.
    assert Pacer(resolve_profile(None), state_dir=tmp_path).is_paused()
    assert _views_used(tmp_path) == 2  # both attempts stay charged
    assert one_browser.built == 1 and one_browser.closed == 1


def test_inaccessible_profile_skips_only_that_contact(
    tmp_path, one_browser, sleeps, monkeypatch
):
    """A 403/404-class miss is isolated: that contact reports `probe_failed`
    with the verbatim reason and the sweep continues in the same session."""

    def _probe(sess, pid):
        if pid == "a":
            raise ProfileInaccessibleError("profile is private or deleted (404)")
        return dict(_STATE)

    monkeypatch.setattr(actions, "get_contact_sync_state", _probe)
    out = worker.contact_sync_states(["a", "b"], state_dir=str(tmp_path))
    assert out["ok"] is True and out["count"] == 2
    assert out["results"][0]["error"] == "probe_failed"
    assert "404" in out["results"][0]["reason"]
    assert out["results"][1]["ok"] is True
    assert one_browser.built == 1


def test_auth_error_stops_and_surfaces(tmp_path, one_browser, sleeps, monkeypatch):
    """A dead session cannot be fixed by probing harder: the sweep stops at the
    auth failure, surfaces it verbatim, and still closes the one browser."""

    def _probe(sess, pid):
        raise AuthenticationError("LinkedIn returned 401 — session expired")

    monkeypatch.setattr(actions, "get_contact_sync_state", _probe)
    out = worker.contact_sync_states(["a", "b"], state_dir=str(tmp_path))
    assert out["ok"] is False and out["error"] == "auth_error"
    assert out["count"] == 1
    assert out["results"][0]["error"] == "auth_error"
    assert one_browser.built == 1 and one_browser.closed == 1


def test_backoff_refuses_before_any_browser(tmp_path, one_browser):
    """A paused ledger refuses the whole sweep BEFORE any browser launches —
    zero LinkedIn traffic, exactly like the single op refusing up front."""
    p = Pacer(resolve_profile(None), state_dir=tmp_path)
    p.pause_for_backoff("LinkedIn returned HTTP 429 (throttled/blocked)")
    p.save()
    out = worker.contact_sync_states(["a", "b"], state_dir=str(tmp_path))
    assert out["ok"] is False and out["error"] == "cap_or_backoff"
    assert out["count"] == 1
    assert out["results"][0]["error"] == "cap_or_backoff"
    assert one_browser.built == 0  # no browser, no network


def test_spent_read_budget_stops_mid_sweep(tmp_path, one_browser, sleeps, monkeypatch):
    """When the profile-view budget runs out mid-batch every later probe would
    refuse identically, so the sweep stops there (the tail stays queued)."""
    from sidecar.packages.referral_outreach.upstream.pacing import PacingProfile

    monkeypatch.setattr(
        actions, "get_contact_sync_state", lambda sess, pid: dict(_STATE)
    )
    profile = PacingProfile(overrides={"profile_views_day": 2})
    out = worker.contact_sync_states(
        ["a", "b", "c", "d"], profile=profile, state_dir=str(tmp_path)
    )
    assert out["ok"] is False and out["error"] == "cap_or_backoff"
    assert out["count"] == 3  # a ok, b ok, c refused — d never emitted
    assert [r.get("error", "") for r in out["results"]] == ["", "", "cap_or_backoff"]
    assert one_browser.built == 1


def test_dry_run_plans_without_browser(tmp_path, one_browser):
    out = worker.contact_sync_states(["a", "b"], state_dir=str(tmp_path), dry_run=True)
    assert out["dry_run"] is True and out["count"] == 2
    assert "ONE browser" in out["plan"]
    assert one_browser.built == 0
