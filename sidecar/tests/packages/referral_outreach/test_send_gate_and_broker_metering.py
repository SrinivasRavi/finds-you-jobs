# voyager_py/tests/test_send_gate_and_broker_metering.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Phase 6: the referral send path over the broker-backed driver.

The full send path is metered and gated INSIDE the GPL package, whether the page
action self-launches its own Chromium or runs on the core browser broker's
serialized surface lane. This proves the account-safety contract holds identically
across both, with ZERO account use (fakes + a fake surface — the wire stays cold):

  1. a send REFUSES before touching the surface when a cap is spent or a backoff
     is active — no session is built, no lane action runs (charge-on-attempt only
     charges an attempt that actually reaches the surface);
  4. a 429 / RateLimited maps to a lane STOP, never a retry ladder: the blocked
     send calls the action exactly once, refunds, and enters backoff, and the
     next send in the batch refuses up front (invariant 6);
  5. the metering is byte-identical self-launched vs broker-backed — the caps and
     charge/refund live in the worker, OUTSIDE `run_browser`, so routing the page
     action onto the broker lane changes nothing the ledger sees;
  - the concurrency-lock probe: two concurrent broker-backed sends share ONE
    file ledger guarded by `pacing._ledger_lock`; the broker rewiring must not
    open a second writer that routes around the load-merge-write.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from sidecar.packages.referral_outreach.upstream import actions, pacing, session, worker
from sidecar.packages.referral_outreach.upstream.errors import (
    RateLimited,
    ReachedConnectionLimit,
)
from sidecar.packages.referral_outreach.upstream.pacing import (
    Pacer,
    PacingProfile,
    resolve_profile,
)


@pytest.fixture
def no_jitter(monkeypatch):
    """Zero the inter-send gap so tests never sleep the 30-90 s pace."""
    monkeypatch.setattr(pacing, "send_delay_seconds", lambda: 0.0)


# --- test doubles -----------------------------------------------------------


class _InlineSession:
    """The self-launch `AccountSession` stand-in: runs the (monkeypatched) page
    action inline on this thread, exactly as the real session does when no broker
    surface is provided. `built` proves whether a session was constructed at all
    (a refused send must build none)."""

    built = 0

    def __init__(self, **kwargs: Any) -> None:
        type(self).built += 1

    def run_browser(self, action):
        return action()

    def close(self) -> None:
        pass


class _FakeSurfaceSession:
    """Duck-typed stand-in for a broker surface's per-lane session. `context` is
    None so `AccountSession._seed_surface_session` early-returns (no cookie work,
    no storage-state) — the metering path under test never depends on it."""

    def __init__(self) -> None:
        self.page = object()
        self.context = None


class _FakeLaneFuture:
    """A `concurrent.futures.Future` stand-in: `.result()` runs the lane callable
    and re-raises whatever it raises, exactly as the real surface lane does."""

    def __init__(self, fn) -> None:
        self._fn = fn

    def result(self, timeout: float | None = None):
        return self._fn(_FakeSurfaceSession())


class _FakeBrokerSurface:
    """A broker `BrowserSurface` stand-in — only `run_on_lane` is exercised by the
    broker-backed `run_browser`. Records how many lane actions ran."""

    def __init__(self) -> None:
        self.lane_runs = 0

    def run_on_lane(self, fn):
        self.lane_runs += 1
        return _FakeLaneFuture(fn)


@pytest.fixture
def counting_session(monkeypatch):
    """Force the self-launch path onto `_InlineSession` and reset its build count."""
    _InlineSession.built = 0
    monkeypatch.setattr(session, "AccountSession", _InlineSession)
    return _InlineSession


# ── 1. refuse before the surface: cap spent / backoff active ────────────────


def test_send_connection_refuses_under_backoff_before_any_surface(
    tmp_path, counting_session, monkeypatch
):
    """An active backoff refuses the invite BEFORE a browser is built or a lane
    action runs — the send never reaches LinkedIn (NFR-LI-02/03)."""
    ran: list[str] = []
    monkeypatch.setattr(
        actions, "send_connection_request",
        lambda s, p, note="": ran.append(p) or ("pending", ""),
    )
    p = Pacer(resolve_profile(None), state_dir=tmp_path)
    p.pause_for_backoff("LinkedIn returned HTTP 429 (throttled/blocked)")
    p.save()

    out = worker.send_connection("someone", state_dir=str(tmp_path))

    assert out["error"] == "cap_or_backoff"
    assert out["sent"] is False
    assert ran == []                       # no lane action reached the surface
    assert _InlineSession.built == 0       # no session/browser built at all


def test_send_connection_refuses_when_invite_cap_spent_before_any_surface(
    tmp_path, counting_session, monkeypatch
):
    """A spent invite cap refuses before the surface — no charge, no lane action."""
    ran: list[str] = []
    monkeypatch.setattr(
        actions, "send_connection_request",
        lambda s, p, note="": ran.append(p) or ("pending", ""),
    )
    # Pin the day cap to 1 and spend it directly on the shared ledger.
    profile = PacingProfile(overrides={"invites_day": 1})
    seed = Pacer(resolve_profile(profile), state_dir=tmp_path)
    seed.record_invite()
    seed.save()
    _InlineSession.built = 0

    out = worker.send_connection("someone", profile=profile, state_dir=str(tmp_path))

    assert out["error"] == "cap_or_backoff"
    assert ran == []
    assert _InlineSession.built == 0
    # The refused send never touched the ledger — still exactly the seeded 1.
    assert Pacer(resolve_profile(profile), state_dir=tmp_path).remaining()["daily_used"] == 1


def test_send_dm_refuses_under_backoff_before_any_surface(
    tmp_path, counting_session, monkeypatch
):
    """The DM path shares the same up-front backoff refusal as the invite path."""
    ran: list[str] = []
    monkeypatch.setattr(
        actions, "send_dm", lambda s, p, m: ran.append(p) or True
    )
    p = Pacer(resolve_profile(None), state_dir=tmp_path)
    p.pause_for_backoff("LinkedIn returned HTTP 429 (throttled/blocked)")
    p.save()

    out = worker.send_dm("someone", "hello", state_dir=str(tmp_path))

    assert out["error"] == "cap_or_backoff"
    assert ran == []
    assert _InlineSession.built == 0


def test_broker_backed_send_also_refuses_before_acquiring_the_surface(
    tmp_path, monkeypatch
):
    """The broker path refuses identically: a paused ledger stops the send before
    the surface provider is ever called, so no lane is acquired (point 1 + 5)."""
    acquired: list[str] = []
    monkeypatch.setattr(
        actions, "send_connection_request", lambda s, p, note="": ("pending", "")
    )
    p = Pacer(resolve_profile(None), state_dir=tmp_path)
    p.pause_for_backoff("LinkedIn returned HTTP 429 (throttled/blocked)")
    p.save()

    def _provider(slug: str) -> Any:
        acquired.append(slug)
        return _FakeBrokerSurface()

    out = worker.send_connection(
        "someone", state_dir=str(tmp_path), surface_provider=_provider
    )

    assert out["error"] == "cap_or_backoff"
    assert acquired == []  # the surface provider was never invoked


# ── 4. a 429 stops the batch and never retries ─────────────────────────────


def test_rate_limit_stops_the_batch_and_never_retries(
    tmp_path, counting_session, no_jitter, monkeypatch
):
    """Invariant 6: a 429 maps to a lane STOP, not a retry ladder. The send that
    hits LinkedIn's weekly-cap dialog calls the action EXACTLY once, refunds the
    attempt, and enters backoff; the batch's next send then refuses up front — the
    batch stops, it never loops."""
    calls: list[str] = []

    def _blocked(sess, pid, note=""):
        calls.append(pid)
        raise ReachedConnectionLimit("Weekly connection limit pop up appeared")

    monkeypatch.setattr(actions, "send_connection_request", _blocked)

    first = worker.send_connection("a", state_dir=str(tmp_path))
    assert first["error"] == "rate_limited"
    assert calls == ["a"]                      # one attempt — no retry ladder
    assert first["sent"] is False
    # Proven no-send → the attempt charge was refunded, and backoff is armed.
    paced = Pacer(resolve_profile(None), state_dir=tmp_path)
    assert paced.is_paused()
    assert paced.remaining()["daily_used"] == 0

    built_after_first = _InlineSession.built
    second = worker.send_connection("b", state_dir=str(tmp_path))
    assert second["error"] == "cap_or_backoff"
    assert calls == ["a"]                       # "b" never reached the surface
    assert _InlineSession.built == built_after_first  # no session for the refused send


def test_dm_rate_limit_stops_without_retry(
    tmp_path, counting_session, no_jitter, monkeypatch
):
    """A DM 429 is the same STOP: one attempt, refund, backoff — no retry loop."""
    calls: list[str] = []

    def _throttled(s, p, m):
        calls.append(p)
        raise RateLimited("LinkedIn returned HTTP 429 (throttled/blocked)")

    monkeypatch.setattr(actions, "send_dm", _throttled)

    out = worker.send_dm("a", "hi", state_dir=str(tmp_path))
    assert out["error"] == "rate_limited"
    assert calls == ["a"]                       # exactly one attempt
    p = Pacer(resolve_profile(None), state_dir=tmp_path)
    assert p.is_paused()
    assert p.remaining()["dm_daily_sent"] == 0  # refunded


# ── 5. metering is identical self-launched vs broker-backed ────────────────


def test_metering_is_identical_self_launched_and_broker_backed(
    tmp_path, no_jitter, monkeypatch
):
    """The caps + charge live in the worker, OUTSIDE `run_browser`, so routing the
    page action onto the broker lane must leave the ledger byte-identical. One
    successful send each way lands exactly one invite charge."""
    monkeypatch.setattr(
        actions, "send_connection_request", lambda s, p, note="": ("pending", "")
    )

    # (a) self-launch: an inline AccountSession stand-in, scoped so the broker leg
    # below drives the REAL AccountSession.
    self_dir = tmp_path / "self"
    with monkeypatch.context() as m:
        m.setattr(session, "AccountSession", _InlineSession)
        out1 = worker.send_connection("a", state_dir=str(self_dir))
    assert out1["ok"] and out1["sent"]
    self_used = Pacer(resolve_profile(None), state_dir=self_dir).remaining()["daily_used"]

    # (b) broker-backed: the REAL AccountSession runs the SAME action on a fake
    # surface lane. Nothing here touches Chromium.
    surface = _FakeBrokerSurface()
    broker_dir = tmp_path / "broker"
    out2 = worker.send_connection(
        "a", state_dir=str(broker_dir), surface_provider=lambda _slug: surface
    )
    assert out2["ok"] and out2["sent"]
    assert surface.lane_runs == 1              # the action really ran on the lane
    broker_used = Pacer(resolve_profile(None), state_dir=broker_dir).remaining()["daily_used"]

    assert self_used == broker_used == 1


# ── concurrency-lock probe: one shared file ledger, no second writer ────────


def test_concurrent_broker_backed_sends_share_the_locked_ledger(
    tmp_path, no_jitter, monkeypatch
):
    """The pacing ledger is a shared file guarded by an inter-process lock
    (`pacing._ledger_lock`) around load-merge-write. The broker rewiring moved the
    page action onto a lane but left the pacer's `save()` — and its lock — in the
    worker thread, so two concurrent sends must not lose a charge (drifting low is
    the unsafe direction). Both sends are broker-backed here, to prove the broker
    path opens NO second writer that routes around the lock."""
    monkeypatch.setattr(
        actions, "send_connection_request", lambda s, p, note="": ("pending", "")
    )
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _send(pid: str) -> None:
        try:
            barrier.wait()  # release both into `send_connection` at once
            worker.send_connection(
                pid, state_dir=str(tmp_path),
                surface_provider=lambda _slug: _FakeBrokerSurface(),
            )
        except BaseException as exc:  # noqa: BLE001 — surface any failure to the assert
            errors.append(exc)

    t1 = threading.Thread(target=_send, args=("a",))
    t2 = threading.Thread(target=_send, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
    # Neither charge was lost: the locked load-merge-write kept both. A ledger that
    # routed around the lock (or a second writer) would drop one → daily_used == 1.
    final = Pacer(resolve_profile(None), state_dir=tmp_path)
    assert final.remaining()["daily_used"] == 2
    assert len(final.state.invites) == 2
