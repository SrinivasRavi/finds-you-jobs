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


def _full(*pids: str) -> list[dict]:
    """Entries for full (metered) probes — no cached urn, degree in question."""
    return [{"public_identifier": p, "urn": "", "thread_only": False} for p in pids]


def _thread(*pids: str) -> list[dict]:
    """Entries for unmetered thread-only probes (host-cached urn)."""
    return [
        {"public_identifier": p, "urn": f"urn:li:fsd_profile:{p}", "thread_only": True}
        for p in pids
    ]


class _CountingSession:
    built = 0
    closed = 0

    def __init__(self, **kwargs):
        type(self).built += 1

    def run_browser(self, action):
        # The per-probe action-on-the-lane bridge — run the (monkeypatched)
        # probe inline, as the self-launch path does with no broker surface.
        return action()

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
    out = worker.contact_sync_states(_full("a", "b", "c"), state_dir=str(tmp_path))
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
    """The first 429 stops the batch (section 0.4): backoff is entered + persisted, the
    remaining contacts are never probed, and the attempted read stays charged
    (charge-on-attempt — it reached LinkedIn)."""
    probed: list[str] = []

    def _probe(sess, pid):
        probed.append(pid)
        if pid == "b":
            raise RateLimited("LinkedIn returned HTTP 429 (throttled/blocked)")
        return dict(_STATE)

    monkeypatch.setattr(actions, "get_contact_sync_state", _probe)
    out = worker.contact_sync_states(_full("a", "b", "c"), state_dir=str(tmp_path))
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
    out = worker.contact_sync_states(_full("a", "b"), state_dir=str(tmp_path))
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
    out = worker.contact_sync_states(_full("a", "b"), state_dir=str(tmp_path))
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
    out = worker.contact_sync_states(_full("a", "b"), state_dir=str(tmp_path))
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
        _full("a", "b", "c", "d"), profile=profile, state_dir=str(tmp_path)
    )
    assert out["ok"] is False and out["error"] == "cap_or_backoff"
    assert out["count"] == 3  # a ok, b ok, c refused — d never emitted
    assert [r.get("error", "") for r in out["results"]] == ["", "", "cap_or_backoff"]
    assert one_browser.built == 1


def test_dry_run_plans_without_browser(tmp_path, one_browser):
    out = worker.contact_sync_states(_full("a", "b"), state_dir=str(tmp_path), dry_run=True)
    assert out["dry_run"] is True and out["count"] == 2
    assert "ONE browser" in out["plan"]
    assert one_browser.built == 0


# --- unmetered thread-only probes (2026-08-16, the read-budget redesign) -----


def test_thread_only_probes_run_first_and_charge_nothing(
    tmp_path, one_browser, sleeps, monkeypatch
):
    """Thread-only entries (cached urn) answer from the sweep's one inbox read:
    they run BEFORE the metered probes, charge zero profile views, and skip
    the inter-read pacing (there is no per-contact request to pace)."""
    order: list[tuple[str, str]] = []

    def _thread_probe(sess, pid, urn):
        order.append(("thread", pid))
        return {**_STATE, "degree": None, "is_first_degree": False,
                "target_urn": urn}

    def _full_probe(sess, pid):
        order.append(("full", pid))
        return {**_STATE, "target_urn": f"urn:li:fsd_profile:{pid}"}

    monkeypatch.setattr(actions, "get_contact_thread_state", _thread_probe)
    monkeypatch.setattr(actions, "get_contact_sync_state", _full_probe)
    out = worker.contact_sync_states(
        _full("cold-1") + _thread("warm-1", "warm-2"), state_dir=str(tmp_path)
    )
    assert out["ok"] is True and out["count"] == 3
    assert order == [("thread", "warm-1"), ("thread", "warm-2"), ("full", "cold-1")]
    # ONLY the full probe charged; the thread pass paid nothing and paced nothing.
    assert _views_used(tmp_path) == 1
    assert sleeps == []
    assert one_browser.built == 1 and one_browser.closed == 1
    by_pid = {r["public_identifier"]: r for r in out["results"]}
    assert by_pid["warm-1"]["degree"] is None  # no profile read this sweep
    assert by_pid["warm-1"]["target_urn"] == "urn:li:fsd_profile:warm-1"
    assert by_pid["cold-1"]["degree"] == 1
    assert by_pid["cold-1"]["target_urn"] == "urn:li:fsd_profile:cold-1"


def test_thread_only_sweep_runs_on_a_spent_ledger(
    tmp_path, one_browser, sleeps, monkeypatch
):
    """The whole point: a spent read budget can no longer silence the
    message-driven columns — thread-only probes still sweep, and only the
    metered tail is refused."""
    from sidecar.packages.referral_outreach.upstream.pacing import PacingProfile

    monkeypatch.setattr(
        actions, "get_contact_thread_state",
        lambda sess, pid, urn: {**_STATE, "degree": None, "target_urn": urn},
    )
    monkeypatch.setattr(
        actions, "get_contact_sync_state", lambda sess, pid: dict(_STATE)
    )
    profile = PacingProfile(overrides={"profile_views_day": 1})
    pacer = Pacer(resolve_profile(profile), state_dir=tmp_path)
    pacer.record_profile_view()  # the day's budget is already gone
    pacer.save()
    out = worker.contact_sync_states(
        _thread("warm-1", "warm-2") + _full("cold-1"),
        profile=profile, state_dir=str(tmp_path),
    )
    assert out["error"] == "cap_or_backoff"  # the metered tail was refused …
    by_pid = {r["public_identifier"]: r for r in out["results"]}
    assert by_pid["warm-1"]["ok"] is True    # … but the thread pass synced
    assert by_pid["warm-2"]["ok"] is True
    assert by_pid["cold-1"]["error"] == "cap_or_backoff"
    assert one_browser.built == 1  # the browser ran for the unmetered work


def test_all_full_sweep_still_refuses_before_any_browser(tmp_path, one_browser):
    """With no unmetered work, a spent ledger still refuses before launching a
    browser — the original zero-traffic refusal is unchanged."""
    from sidecar.packages.referral_outreach.upstream.pacing import PacingProfile

    profile = PacingProfile(overrides={"profile_views_day": 1})
    pacer = Pacer(resolve_profile(profile), state_dir=tmp_path)
    pacer.record_profile_view()
    pacer.save()
    out = worker.contact_sync_states(
        _full("a", "b"), profile=profile, state_dir=str(tmp_path)
    )
    assert out["ok"] is False and out["error"] == "cap_or_backoff"
    assert one_browser.built == 0


def test_thread_only_without_urn_falls_back_to_a_full_probe(
    tmp_path, one_browser, sleeps, monkeypatch
):
    """A `thread_only` entry missing its urn is the bootstrap case: it runs as
    a metered full probe (which resolves and returns the urn to cache)."""
    full_probed: list[str] = []

    def _full_probe(sess, pid):
        full_probed.append(pid)
        return {**_STATE, "target_urn": f"urn:li:fsd_profile:{pid}"}

    monkeypatch.setattr(actions, "get_contact_sync_state", _full_probe)
    out = worker.contact_sync_states(
        [{"public_identifier": "warm-1", "urn": "", "thread_only": True}],
        state_dir=str(tmp_path),
    )
    assert full_probed == ["warm-1"]
    assert _views_used(tmp_path) == 1
    assert out["results"][0]["target_urn"] == "urn:li:fsd_profile:warm-1"


def test_throttled_inbox_read_stops_the_whole_sweep(
    tmp_path, one_browser, sleeps, monkeypatch
):
    """A 429 on the one inbox read during the thread pass enters backoff and
    stops everything — the metered tail would 429 identically."""
    monkeypatch.setattr(
        actions, "get_contact_thread_state",
        lambda sess, pid, urn: (_ for _ in ()).throw(
            RateLimited("LinkedIn returned HTTP 429 (throttled/blocked)")
        ),
    )
    full_probed: list[str] = []
    monkeypatch.setattr(
        actions, "get_contact_sync_state",
        lambda sess, pid: full_probed.append(pid) or dict(_STATE),
    )
    out = worker.contact_sync_states(
        _thread("warm-1") + _full("cold-1"), state_dir=str(tmp_path)
    )
    assert out["ok"] is False and out["error"] == "rate_limited"
    assert full_probed == []  # the metered tail never ran
    assert Pacer(resolve_profile(None), state_dir=tmp_path).is_paused()
    assert _views_used(tmp_path) == 0  # a thread probe charges nothing, even failing
