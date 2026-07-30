# voyager_py/pacing.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW code written for the finds-you-jobs fork (GPL, lives in the GPL subtree).
# Derived from OpenOutreach's pacing philosophy @ a7a9101:
#   - conf.py: DEFAULT_CONNECT_DAILY_LIMIT / DEFAULT_CONNECT_WEEKLY_LIMIT,
#     MIN_DELAY/MAX_DELAY, HUMAN_TYPE_* delays, active-hours window.
#   - browser/session.py: random_sleep() jitter between actions.
# The rolling-window ledger, tiered caps, and 24 h backoff flag are our own —
# they make caps + pacing OWNED AND ENFORCED inside this subprocess, which is
# the finds-you-jobs contract (ROADMAP §66, NFR-LI-01/02/03, FR-NW-04/05). The
# host queries the remaining quota and never re-implements or overrides it.
"""Account-safety pacing: tiered rolling caps, jittered send delays, backoff.

State is persisted to a JSON file so caps survive across one-shot CLI
invocations (each `python -m voyager_py <cmd>` is a fresh process). All limits
here are voyager_py-owned; the numbers are illustrative OpenOutreach-style
defaults, not a promise LinkedIn honours.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

# --- human-paced composition (upstream conf.py HUMAN_TYPE_*_DELAY_MS) ---
HUMAN_TYPE_MIN_DELAY_MS = 50
HUMAN_TYPE_MAX_DELAY_MS = 200

# --- inter-send jitter (US-REF-04 / NFR-LI-01: 30–90 s, jittered) ---
SEND_DELAY_MIN_S = 30.0
SEND_DELAY_MAX_S = 90.0

# --- backoff window after a rate-limit signal (NFR-LI-03: ≈ 24 h) ---
BACKOFF_SECONDS = 24 * 60 * 60

DAY_SECONDS = 24 * 60 * 60
WEEK_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class Tier:
    """A user-selectable account tier. Caps count connection-requests-with-note
    (2nd/3rd-degree invites); 1st-degree DMs are tracked separately and never
    decrement these (FR-NW-04)."""

    name: str
    daily: int
    weekly: int


# Two tiers, global across jobs (FR-NW-04 / US-REF-08). Numbers are illustrative
# OpenOutreach-style defaults; New is the safe default for a fresh account.
TIERS: dict[str, Tier] = {
    "new": Tier("new", daily=15, weekly=100),
    "seasoned": Tier("seasoned", daily=30, weekly=200),
}
DEFAULT_TIER = "new"


def resolve_tier(name: str | None) -> Tier:
    key = (name or DEFAULT_TIER).strip().lower()
    if key not in TIERS:
        raise ValueError(f"unknown tier {name!r}; choose one of {sorted(TIERS)}")
    return TIERS[key]


def human_type_delay_ms() -> int:
    """One randomized per-keystroke delay (mimics human typing)."""
    return random.randint(HUMAN_TYPE_MIN_DELAY_MS, HUMAN_TYPE_MAX_DELAY_MS)


def send_delay_seconds() -> float:
    """One jittered inter-send pause. Callers sleep this between sends."""
    return random.uniform(SEND_DELAY_MIN_S, SEND_DELAY_MAX_S)


@dataclass
class PacingState:
    """Persisted pacing ledger. `invites` are epoch-seconds timestamps of
    connection-requests-with-note (the capped action); `dms` are 1st-degree
    referral-ask DMs (tracked, uncapped). `paused_until` is the backoff
    deadline (epoch seconds), 0 when not paused."""

    invites: list[float] = field(default_factory=list)
    dms: list[float] = field(default_factory=list)
    paused_until: float = 0.0
    paused_reason: str = ""

    def to_json(self) -> dict:
        return {
            "invites": self.invites,
            "dms": self.dms,
            "paused_until": self.paused_until,
            "paused_reason": self.paused_reason,
        }

    @classmethod
    def from_json(cls, data: dict) -> PacingState:
        return cls(
            invites=list(data.get("invites", [])),
            dms=list(data.get("dms", [])),
            paused_until=float(data.get("paused_until", 0.0)),
            paused_reason=str(data.get("paused_reason", "")),
        )


def default_state_dir() -> Path:
    """Where the pacing ledger lives. Env override first (the host sets this so
    each user/account gets its own ledger), else a per-user cache dir."""
    env = os.environ.get("VOYAGER_STATE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "findsyoujobs" / "voyager"


def _count_within(timestamps: list[float], window_s: float, now: float) -> int:
    cutoff = now - window_s
    return sum(1 for t in timestamps if t >= cutoff)


def _prune(timestamps: list[float], now: float) -> list[float]:
    """Drop entries older than the weekly window (the widest we account on)."""
    cutoff = now - WEEK_SECONDS
    return [t for t in timestamps if t >= cutoff]


class Pacer:
    """Owns caps + backoff for one account. Load → inspect/record → save.

    All authority lives here (NFR-LI-02): the host asks `remaining()`, disables
    UI on what we report, and calls `record_invite()` / `pause_for_backoff()`;
    it never re-derives or overrides the numbers.
    """

    STATE_FILENAME = "pacing_state.json"

    def __init__(self, tier: Tier, state_dir: Path | None = None) -> None:
        self.tier = tier
        self.state_dir = state_dir or default_state_dir()
        self.state = self._load()

    # --- persistence ---
    @property
    def _state_path(self) -> Path:
        return self.state_dir / self.STATE_FILENAME

    def _load(self) -> PacingState:
        try:
            data = json.loads(self._state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return PacingState()
        return PacingState.from_json(data)

    def save(self, now: float | None = None) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        now = time.time() if now is None else now
        self.state.invites = _prune(self.state.invites, now)
        self.state.dms = _prune(self.state.dms, now)
        self._state_path.write_text(json.dumps(self.state.to_json(), indent=2))

    # --- backoff (NFR-LI-03) ---
    def is_paused(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.state.paused_until > now

    def pause_for_backoff(self, reason: str, now: float | None = None) -> float:
        """Enter voyager-owned backoff after a rate-limit signal. Returns the
        deadline (epoch seconds)."""
        now = time.time() if now is None else now
        self.state.paused_until = now + BACKOFF_SECONDS
        self.state.paused_reason = reason
        return self.state.paused_until

    def resume(self) -> None:
        """Manual override (Settings → Networking resume button)."""
        self.state.paused_until = 0.0
        self.state.paused_reason = ""

    # --- caps (FR-NW-04 / NFR-LI-02) ---
    def remaining(self, now: float | None = None) -> dict:
        """The live quota the host displays and gates the popup on."""
        now = time.time() if now is None else now
        used_day = _count_within(self.state.invites, DAY_SECONDS, now)
        used_week = _count_within(self.state.invites, WEEK_SECONDS, now)
        daily_remaining = max(0, self.tier.daily - used_day)
        weekly_remaining = max(0, self.tier.weekly - used_week)
        return {
            "tier": self.tier.name,
            "daily_cap": self.tier.daily,
            "weekly_cap": self.tier.weekly,
            "daily_used": used_day,
            "weekly_used": used_week,
            "daily_remaining": daily_remaining,
            "weekly_remaining": weekly_remaining,
            # 1st-degree DMs: tracked + reported, never capped (FR-NW-04 —
            # "DMs do not decrement the invite counter"). Surfaced so a sent DM
            # is visible in the quota view instead of reading as "0 used".
            "dm_daily_sent": _count_within(self.state.dms, DAY_SECONDS, now),
            "dm_weekly_sent": _count_within(self.state.dms, WEEK_SECONDS, now),
            # what an over-cap-aware caller may still send right now:
            "invites_available": min(daily_remaining, weekly_remaining),
            "paused": self.is_paused(now),
            "paused_until": self.state.paused_until,
            "paused_reason": self.state.paused_reason,
        }

    def can_send_invite(self, now: float | None = None) -> tuple[bool, str]:
        """(allowed, reason). Enforced here before any network call."""
        now = time.time() if now is None else now
        if self.is_paused(now):
            return False, (
                f"voyager paused until {self.state.paused_until:.0f} "
                f"({self.state.paused_reason or 'rate-limit backoff'})"
            )
        r = self.remaining(now)
        if r["daily_remaining"] <= 0:
            return False, f"daily cap reached ({self.tier.daily}/day, tier={self.tier.name})"
        if r["weekly_remaining"] <= 0:
            return False, f"weekly cap reached ({self.tier.weekly}/wk, tier={self.tier.name})"
        return True, ""

    def can_send_dm(self, now: float | None = None) -> tuple[bool, str]:
        """1st-degree DMs are uncapped but still blocked during backoff."""
        now = time.time() if now is None else now
        if self.is_paused(now):
            return False, (
                f"voyager paused until {self.state.paused_until:.0f} "
                f"({self.state.paused_reason or 'rate-limit backoff'})"
            )
        return True, ""

    def record_invite(self, now: float | None = None) -> None:
        self.state.invites.append(time.time() if now is None else now)

    def record_dm(self, now: float | None = None) -> None:
        self.state.dms.append(time.time() if now is None else now)

    # --- inter-send spacing (NFR-LI-01) ---
    def last_send_at(self) -> float:
        """Epoch seconds of the most recent outbound action of ANY kind, 0 if none.
        Invites and DMs share one clock: LinkedIn sees one account, so spacing
        must span both even though only invites decrement a cap."""
        return max(
            self.state.invites[-1] if self.state.invites else 0.0,
            self.state.dms[-1] if self.state.dms else 0.0,
        )

    def seconds_until_next_send(self, now: float | None = None) -> float:
        """How long the caller MUST sleep before the next send, 0 if enough time
        has already elapsed.

        This is the enforcement half of `send_delay_seconds()`, which for a long
        time was only ever *reported* as a `delay_hint_s` field that nothing slept
        on — so batched sends went out back-to-back at machine pace. The gap is
        re-jittered on every call so a batch never settles into a fixed rhythm,
        and it is derived from the persisted ledger rather than in-process state,
        so it holds across the separate one-shot `send` operations the runner
        dispatches for a batch.
        """
        now = time.time() if now is None else now
        last = self.last_send_at()
        if last <= 0.0:
            return 0.0  # first send of this account's life — nothing to space from
        return max(0.0, (last + send_delay_seconds()) - now)

    def wait_before_send(self, now: float | None = None, *, sleep=time.sleep) -> float:
        """Block for `seconds_until_next_send()` and return what was actually
        slept. Safe to call from an operation thread (the runner's
        ThreadPoolExecutor) — never from the event loop."""
        wait = self.seconds_until_next_send(now)
        if wait > 0:
            sleep(wait)
        return wait
