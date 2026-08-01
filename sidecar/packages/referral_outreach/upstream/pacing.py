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
invocations (each `python -m voyager_py <cmd>` is a fresh process), and so a
batch dispatched as N separate operations still shares one ledger.

All limits here are voyager_py-owned. They are set at 50-70% of the *estimated*
LinkedIn ceiling — estimated because LinkedIn publishes almost none of them.
They are not a promise LinkedIn honours, and not a safety guarantee; they are
harm reduction against someone else's undocumented limit. Derivation and sources:
`docs/internal/linkedin-posture.md` §4.
"""

from __future__ import annotations

import contextlib
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

# --- pause between consecutive pages of a read the user is waiting on ---
# Short on purpose: the send jitter band (30-90 s) is right for irreversible
# outbound actions and absurd for paging a job search. The point is that pages
# are not issued at machine speed, not that the user waits minutes.
PAGE_PAUSE_RANGE_S = (0.8, 2.2)

# --- pause between consecutive profile fetches inside discovery enrichment ---
# Matches the tool we forked: OpenOutreach sleeps uniform(6, 10) s per scraped
# profile (`linkedin/db/leads.py` @ a7a9101). Bulk profile reads are the axis
# scraping detection actually keys on, and until this constant existed we were
# strictly MORE aggressive there than upstream (posture doc §2).
ENRICH_PAUSE_RANGE_S = (6.0, 10.0)

# The package-owned ceiling on one logged-in job search (one page of LinkedIn's
# own page size). Enforced in `worker.search_jobs` so no host request can turn
# one click into a multi-hundred-row authenticated crawl.
MAX_JOBS_PER_SEARCH = 25

# --- backoff window after a rate-limit signal (NFR-LI-03: ≈ 24 h) ---
BACKOFF_SECONDS = 24 * 60 * 60

HOUR_SECONDS = 60 * 60
DAY_SECONDS = 24 * 60 * 60
WEEK_SECONDS = 7 * 24 * 60 * 60
# The Commercial Use Limit resets midnight PST on the 1st of each calendar
# month. We meter it on a rolling 31-day window instead: strictly MORE
# conservative than a calendar month (it never hands back allowance early), and
# it needs no timezone handling in a ledger that stores bare epoch seconds.
MONTH_SECONDS = 31 * 24 * 60 * 60
# Free-plan personalized invitation notes are commonly reported as ~5/month.
# Rolling 30 days, same reasoning as above.
NOTE_WINDOW_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class Budget:
    """One meter's ceiling. `None` means "this meter has no limit of that kind".

    `hour`/`day`/`week` are rolling windows measured back from now — LinkedIn's
    invitation quota is itself a rolling 7-day window, not a calendar week. `hour`
    exists for the logged-in job-search page meter (self-imposed pages/hour).
    """

    hour: int | None = None
    day: int | None = None
    week: int | None = None
    month: int | None = None

    def scaled(self, risk_pct: int) -> Budget:
        """This ceiling scaled to `risk_pct` percent of itself, each window
        rounded and floored at 1 so a live meter never collapses to 0 (a 0-cap
        meter would refuse every action; the floor keeps low risk usable). A
        `None` window stays `None` — scaling an absent limit is still absent."""
        def s(v: int | None) -> int | None:
            return None if v is None else max(1, round(v * risk_pct / 100))
        return Budget(hour=s(self.hour), day=s(self.day),
                      week=s(self.week), month=s(self.month))


@dataclass(frozen=True)
class Tier:
    """A user-selectable account tier: one budget per metered LinkedIn action.

    Every number here is OUR enforced soft cap, set at 50–70% of the *estimated*
    LinkedIn ceiling. LinkedIn publishes none of those ceilings (only the 30,000
    connection cap, the restriction ladder, and the CUL reset date), so the
    estimates are corroborated vendor observation with no primary confirmation
    and LinkedIn changes them without notice. See
    `docs/internal/linkedin-posture.md` §4 for the derivation table and
    `linkedin-limits-audit-2026-07-29.md` for the source audit.

    Nothing here is a promise about account safety. It is harm reduction against
    someone else's undocumented limit.
    """

    name: str
    invites: Budget
    dms: Budget
    profile_views: Budget
    searches: Budget
    notes: Budget
    # Self-imposed logged-in job-search ceiling, pages/hour (25 jobs/page). NOT a
    # LinkedIn limit — job search is CUL-exempt (help `a564226`); this is purely
    # our own throttle so one sitting can't fan a role list into a large
    # authenticated burst. Defaults to no limit for legacy `TIERS` (which predate
    # it); the membership ceilings below set it.
    job_search_pages: Budget = field(default_factory=Budget)

    # Back-compat accessors: the app-side DTO and the typed facade still speak
    # "daily/weekly" for invites, the only meter that existed before.
    @property
    def daily(self) -> int:
        return self.invites.day or 0

    @property
    def weekly(self) -> int:
        return self.invites.week or 0


# Global across jobs (FR-NW-04 / US-REF-08). `new` is the default for a fresh
# account; `recovering` is entered automatically after repeat restrictions.
#
# Derivation (estimated LinkedIn ceiling → ours):
#   invites/wk    ~50 new · ~100 established  → 30 (×0.60) · 65 (×0.65) · 15
#   invites/day   15–25 safe band, LOW end    →  8 ·  10 ·  3   (weekly stays binding)
#   dms           NO corroborated limit       → policy cap, ours alone, not a %
#   profile views 80 (disputed low) – 500     → 25 (×0.31) · 50 (×0.625) · 10
#   searches (CUL) ~250–350 / month           → 150 (×0.60). Job search is EXEMPT.
#   notes (free)  ~5 / month                  →  3 (×0.60)
TIERS: dict[str, Tier] = {
    "new": Tier(
        "new",
        invites=Budget(day=8, week=30),
        dms=Budget(day=10, week=50),
        profile_views=Budget(day=25),
        searches=Budget(month=150),
        notes=Budget(month=3),
    ),
    "seasoned": Tier(
        "seasoned",
        invites=Budget(day=10, week=65),
        dms=Budget(day=25, week=120),
        profile_views=Budget(day=50),
        searches=Budget(month=150),
        notes=Budget(month=3),
    ),
    "recovering": Tier(
        "recovering",
        invites=Budget(day=3, week=15),
        dms=Budget(day=5, week=20),
        profile_views=Budget(day=10),
        searches=Budget(month=150),
        notes=Budget(month=3),
    ),
}
DEFAULT_TIER = "new"

# Which ledger each metered action writes to, and the widest window that ledger
# is pruned to. Pruning used to be a flat one week for everything, which would
# have silently destroyed the 30-day note ledger and the monthly CUL ledger.
METER_WINDOWS: dict[str, int] = {
    "invites": WEEK_SECONDS,
    "dms": WEEK_SECONDS,
    "profile_views": WEEK_SECONDS,  # budgeted daily; a week retained for reporting
    "searches": MONTH_SECONDS,
    "notes": NOTE_WINDOW_SECONDS,
    "job_search_pages": DAY_SECONDS,  # budgeted hourly; a day retained for reporting
}

# Every window a Budget can express, longest first for `usage()` reporting.
BUDGET_WINDOWS: tuple[tuple[str, int], ...] = (
    ("hour", HOUR_SECONDS),
    ("day", DAY_SECONDS),
    ("week", WEEK_SECONDS),
    ("month", MONTH_SECONDS),
)


def resolve_tier(name: str | None) -> Tier:
    key = (name or DEFAULT_TIER).strip().lower()
    if key not in TIERS:
        raise ValueError(f"unknown tier {name!r}; choose one of {sorted(TIERS)}")
    return TIERS[key]


# ---------------------------------------------------------------------------
# Membership × risk% × per-meter override (maintainer directive 2026-08-01)
# ---------------------------------------------------------------------------
#
# The user selects a LinkedIn **membership type** and a **risk appetite** (0-100
# %). Each membership carries an *estimated LinkedIn ceiling* per meter — the
# "100 %" reference. The self-imposed cap = ceiling × risk%, unless a per-meter
# override pins an absolute number. This REPLACES the fixed new/seasoned tier as
# the app-facing basis (the risk slider now expresses "new vs seasoned"): the
# host stores (membership, risk_pct, overrides) and asks THIS module for the
# numbers — the caps stay owned and enforced here (NFR-LI-02).
#
# **Every ceiling below is our estimate; LinkedIn publishes almost none of them**
# (only the 30 000 connection cap, the CUL reset date, and the restriction
# ladder). The Free column is calibrated so `ceiling × DEFAULT_RISK_PCT`
# reproduces the old `TIERS["new"]` caps EXACTLY, so switching to this system
# changes nothing until the user moves the slider. The non-Free columns are
# coarser — LinkedIn's plan-tier limits are even less documented — and are
# deliberately conservative. Nothing here is a promise about account safety; it
# is harm reduction against someone else's undocumented limit. Derivation and
# sources: `docs/internal/linkedin-posture.md` §4 and
# `linkedin-limits-audit-2026-07-29.md`.
#
#   meter          Free (100%)         basis
#   invites        13/day · 50/wk      account-level, not plan → same across plans
#   dms            17/day · 83/wk      Premium/SalesNav/Recruiter add InMail credits
#   profile views  42/day              80 (disputed low) – 500; scales with plan
#   searches(CUL)  250/month           Premium/SalesNav/Recruiter higher or exempt
#   notes          5/month             free ~5; paid plans effectively unlimited
#   job pages/hr   7/hour              OUR throttle; job search is CUL-exempt, plan-independent
DEFAULT_MEMBERSHIP = "free"
DEFAULT_RISK_PCT = 60
MIN_RISK_PCT = 10  # below this a live meter rounds toward unusable; clamp up
MAX_RISK_PCT = 100  # 100% = sitting at the estimated LinkedIn ceiling (max risk)

CEILINGS: dict[str, Tier] = {
    "free": Tier(
        "free",
        invites=Budget(day=13, week=50),
        dms=Budget(day=17, week=83),
        profile_views=Budget(day=42),
        searches=Budget(month=250),
        notes=Budget(month=5),
        job_search_pages=Budget(hour=7),
    ),
    "premium": Tier(
        "premium",
        invites=Budget(day=13, week=50),
        dms=Budget(day=25, week=125),
        profile_views=Budget(day=83),
        searches=Budget(month=500),
        notes=Budget(month=100),  # effectively unlimited on paid plans
        job_search_pages=Budget(hour=7),
    ),
    "sales_navigator": Tier(
        "sales_navigator",
        invites=Budget(day=13, week=50),
        dms=Budget(day=33, week=160),
        profile_views=Budget(day=125),
        searches=Budget(month=800),
        notes=Budget(month=100),
        job_search_pages=Budget(hour=7),
    ),
    "recruiter_lite": Tier(
        "recruiter_lite",
        invites=Budget(day=13, week=50),
        dms=Budget(day=42, week=200),
        profile_views=Budget(day=165),
        searches=Budget(month=1000),
        notes=Budget(month=100),
        job_search_pages=Budget(hour=7),
    ),
}
MEMBERSHIPS: tuple[str, ...] = tuple(CEILINGS)

# Every (meter, window) pair the user can override, in display order. The key
# form `"{meter}_{window}"` is the wire/storage contract for overrides.
OVERRIDABLE: tuple[tuple[str, str], ...] = (
    ("invites", "day"), ("invites", "week"),
    ("dms", "day"), ("dms", "week"),
    ("profile_views", "day"),
    ("searches", "month"),
    ("notes", "month"),
    ("job_search_pages", "hour"),
)


def resolve_membership(name: str | None) -> Tier:
    """The 100% estimated-ceiling Tier for a membership (unscaled)."""
    key = (name or DEFAULT_MEMBERSHIP).strip().lower()
    if key not in CEILINGS:
        raise ValueError(
            f"unknown membership {name!r}; choose one of {sorted(CEILINGS)}"
        )
    return CEILINGS[key]


def clamp_risk(risk_pct: int | None) -> int:
    """Risk into [MIN, MAX]. None → the conservative default."""
    if risk_pct is None:
        return DEFAULT_RISK_PCT
    return max(MIN_RISK_PCT, min(MAX_RISK_PCT, int(risk_pct)))


@dataclass(frozen=True)
class PacingProfile:
    """The user's three inputs the host stores and hands to this module. The
    numbers are derived HERE, never by the host: `resolve_profile` turns these
    into the effective `Tier` the `Pacer` enforces."""

    membership: str = DEFAULT_MEMBERSHIP
    risk_pct: int = DEFAULT_RISK_PCT
    # {"invites_week": 42, "job_search_pages_hour": 3, ...} — absolute pins that
    # win over the scaled ceiling for that one (meter, window).
    overrides: dict[str, int] = field(default_factory=dict)


def _apply_override(
    budget: Budget, ceiling: Budget, meter: str, overrides: dict[str, int]
) -> Budget:
    """Return `budget` (the scaled cap) with any per-window override for `meter`
    applied. An override is an absolute pin, clamped to `[0, ceiling]` — it can
    never exceed our estimate of LinkedIn's own limit (the UI offers only
    in-range values via a dropdown; this is the enforcement backstop, so the
    "never above the estimated max" guarantee holds whatever a client sends). A
    window with no override keeps its scaled value."""
    changed: dict[str, int] = {}
    for window in ("hour", "day", "week", "month"):
        key = f"{meter}_{window}"
        cap = getattr(ceiling, window)
        if key in overrides and getattr(budget, window) is not None:
            value = max(0, int(overrides[key]))
            changed[window] = min(value, cap) if cap is not None else value
    if not changed:
        return budget
    return Budget(
        hour=changed.get("hour", budget.hour),
        day=changed.get("day", budget.day),
        week=changed.get("week", budget.week),
        month=changed.get("month", budget.month),
    )


def resolve_profile(profile: PacingProfile | None) -> Tier:
    """Membership ceilings × risk%, with per-meter overrides applied → the
    effective `Tier` the `Pacer` enforces. This is the one place the numbers are
    computed; the host passes only the three inputs."""
    p = profile or PacingProfile()
    risk = clamp_risk(p.risk_pct)
    base = resolve_membership(p.membership)
    ov = p.overrides or {}
    return Tier(
        name=base.name,
        invites=_apply_override(base.invites.scaled(risk), base.invites, "invites", ov),
        dms=_apply_override(base.dms.scaled(risk), base.dms, "dms", ov),
        profile_views=_apply_override(
            base.profile_views.scaled(risk), base.profile_views, "profile_views", ov),
        searches=_apply_override(base.searches.scaled(risk), base.searches, "searches", ov),
        notes=_apply_override(base.notes.scaled(risk), base.notes, "notes", ov),
        job_search_pages=_apply_override(
            base.job_search_pages.scaled(risk), base.job_search_pages, "job_search_pages", ov),
    )


def default_overrides(membership: str | None, risk_pct: int | None) -> dict[str, int]:
    """The computed (ceiling × risk%) value for every overridable (meter,
    window) — i.e. what the per-meter override boxes reset TO when the
    membership or the risk slider changes. The host writes exactly this back so
    a stored override always shows the current basis's default until edited."""
    risk = clamp_risk(risk_pct)
    base = resolve_membership(membership)
    out: dict[str, int] = {}
    for meter, window in OVERRIDABLE:
        scaled = getattr(getattr(base, meter).scaled(risk), window)
        if scaled is not None:
            out[f"{meter}_{window}"] = scaled
    return out


def human_type_delay_ms() -> int:
    """One randomized per-keystroke delay (mimics human typing)."""
    return random.randint(HUMAN_TYPE_MIN_DELAY_MS, HUMAN_TYPE_MAX_DELAY_MS)


def send_delay_seconds() -> float:
    """One jittered inter-send pause. Callers sleep this between sends."""
    return random.uniform(SEND_DELAY_MIN_S, SEND_DELAY_MAX_S)


@dataclass
class PacingState:
    """Persisted pacing ledger — one epoch-seconds timestamp list per meter.

    `invites` are connection requests, `dms` 1st-degree messages, `profile_views`
    authenticated profile fetches, `searches` CUL-counted searches (People and
    company search — job search is exempt and charges nothing), `notes` the
    free-plan personalized invitation notes. `paused_until` is the backoff
    deadline (epoch seconds), 0 when not paused.

    Older ledgers on disk carry only `invites`/`dms`; the new meters default to
    empty, so an existing install upgrades with its invite history intact.
    """

    invites: list[float] = field(default_factory=list)
    dms: list[float] = field(default_factory=list)
    profile_views: list[float] = field(default_factory=list)
    searches: list[float] = field(default_factory=list)
    notes: list[float] = field(default_factory=list)
    # Logged-in job-search page fetches — budgeted hourly (self-imposed throttle).
    # Older ledgers predate it and default empty, so an install upgrades cleanly.
    job_search_pages: list[float] = field(default_factory=list)
    paused_until: float = 0.0
    paused_reason: str = ""

    def meter(self, name: str) -> list[float]:
        return getattr(self, name)  # KeyError-equivalent via AttributeError

    def to_json(self) -> dict:
        return {
            "invites": self.invites,
            "dms": self.dms,
            "profile_views": self.profile_views,
            "searches": self.searches,
            "notes": self.notes,
            "job_search_pages": self.job_search_pages,
            "paused_until": self.paused_until,
            "paused_reason": self.paused_reason,
        }

    @classmethod
    def from_json(cls, data: dict) -> PacingState:
        return cls(
            invites=list(data.get("invites", [])),
            dms=list(data.get("dms", [])),
            profile_views=list(data.get("profile_views", [])),
            searches=list(data.get("searches", [])),
            notes=list(data.get("notes", [])),
            job_search_pages=list(data.get("job_search_pages", [])),
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


def _prune(timestamps: list[float], now: float, window_s: float = WEEK_SECONDS) -> list[float]:
    """Drop entries older than that meter's widest accounting window. Per-meter,
    because a flat one-week prune would silently erase the 30-day note ledger and
    the monthly CUL ledger the moment they were added."""
    cutoff = now - window_s
    return [t for t in timestamps if t >= cutoff]


@contextlib.contextmanager
def _ledger_lock(lock_path: Path):
    """Advisory inter-meter file lock held only around load-merge-write in
    `Pacer.save()` (never across browser work).

    Needed since reads became metered: `send`, `discover`, `contact_sync` and
    the logged-in job search run in SEPARATE runner concurrency groups, so two
    operations can hold the ledger in memory at once — and an unlocked
    read-modify-write would let a contact-sync save erase the invite a
    concurrent send just recorded (under-counting, the unsafe direction).
    POSIX `flock` / Windows `msvcrt.locking`, blocking; both release on fd close
    even if the process dies."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    try:
        if os.name == "nt":  # pragma: no cover — exercised on Windows CI only
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":  # pragma: no cover
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


class Pacer:
    """Owns caps + backoff for one account. Load → inspect/record → save.

    All authority lives here (NFR-LI-02): the host asks `remaining()`, disables
    UI on what we report, and calls `record_invite()` / `pause_for_backoff()`;
    it never re-derives or overrides the numbers.
    """

    STATE_FILENAME = "pacing_state.json"
    LOCK_FILENAME = "pacing_state.lock"

    def __init__(self, tier: Tier, state_dir: Path | None = None) -> None:
        self.tier = tier
        self.state_dir = state_dir or default_state_dir()
        self.state = self._load()
        # Events THIS pacer recorded since the last save, replayed onto a fresh
        # read of the file at save time — so a concurrent operation's save can
        # never erase them, and ours never erases theirs. (meter, epoch-seconds).
        self._pending: list[tuple[str, float]] = []
        # Every event this pacer ever recorded (survives saves) — the refund
        # authority: only charges we made ourselves may be given back.
        self._mine: list[tuple[str, float]] = []
        # Own already-saved events to REMOVE at next save (a refund issued after
        # the attempt charge was persisted — e.g. charge → save → browser →
        # proven no-send → refund).
        self._refunds: list[tuple[str, float]] = []
        # This pacer's explicit backoff intent, applied at save time. None → no
        # opinion (keep whatever is on disk); ("pause", deadline, reason) merges
        # by max-deadline; ("resume",) clears unconditionally — a manual resume
        # must beat a stale on-disk deadline or the button does nothing.
        self._pause_action: tuple | None = None

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
        """Merge this pacer's recorded events into the on-disk ledger.

        Load-merge-write under an inter-process lock, then an atomic
        `os.replace` — a concurrent save (send vs contact-sync vs job search run
        in different runner groups) can neither tear the file nor lose the other
        side's charges. In-memory `self.state` becomes the merged view."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        now = time.time() if now is None else now
        with _ledger_lock(self.state_dir / self.LOCK_FILENAME):
            merged = self._load()
            for meter, at in self._pending:
                merged.meter(meter).append(at)
            for meter, at in self._refunds:
                with contextlib.suppress(ValueError):
                    merged.meter(meter).remove(at)
            if self._pause_action is not None:
                if self._pause_action[0] == "resume":
                    merged.paused_until, merged.paused_reason = 0.0, ""
                elif self._pause_action[1] > merged.paused_until:
                    merged.paused_until = self._pause_action[1]
                    merged.paused_reason = self._pause_action[2]
            for meter, window in METER_WINDOWS.items():
                setattr(merged, meter, _prune(sorted(merged.meter(meter)), now, window))
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(merged.to_json(), indent=2))
            os.replace(tmp, self._state_path)
        self.state = merged
        self._pending = []
        self._refunds = []
        self._pause_action = None

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
        self._pause_action = ("pause", self.state.paused_until, reason)
        return self.state.paused_until

    def resume(self) -> None:
        """Manual override (Settings → Networking resume button)."""
        self.state.paused_until = 0.0
        self.state.paused_reason = ""
        self._pause_action = ("resume",)

    # --- caps (FR-NW-04 / NFR-LI-02) ---
    def _paused_reason(self) -> str:
        return (
            f"voyager paused until {self.state.paused_until:.0f} "
            f"({self.state.paused_reason or 'rate-limit backoff'})"
        )

    def _budget_for(self, meter: str) -> Budget:
        return getattr(self.tier, meter)

    def usage(self, meter: str, now: float | None = None) -> dict:
        """Used / cap / remaining for one meter, per window it budgets on."""
        now = time.time() if now is None else now
        budget, stamps = self._budget_for(meter), self.state.meter(meter)
        out: dict[str, int | None] = {}
        for window_name, window_s in BUDGET_WINDOWS:
            cap = getattr(budget, window_name)
            if cap is None:
                continue
            # `notes` budgets on `month` but is retained on a 30-day window.
            if meter == "notes" and window_name == "month":
                window_s = NOTE_WINDOW_SECONDS
            used = _count_within(stamps, window_s, now)
            out[f"{window_name}_cap"] = cap
            out[f"{window_name}_used"] = used
            out[f"{window_name}_remaining"] = max(0, cap - used)
        return out

    def check(self, meter: str, now: float | None = None) -> tuple[bool, str]:
        """(allowed, reason) for any metered action.

        Backoff blocks EVERY meter, reads included. That is the point: a backoff
        that only stopped sends left the app running People searches and profile
        fetches — exactly the reads the restriction ladder watches — while
        LinkedIn was already telling us to stop.
        """
        now = time.time() if now is None else now
        if self.is_paused(now):
            return False, self._paused_reason()
        u = self.usage(meter, now)
        for window_name in ("hour", "day", "week", "month"):
            if u.get(f"{window_name}_remaining") == 0:
                cap = u[f"{window_name}_cap"]
                return False, (
                    f"{meter} cap reached ({cap}/{window_name}, plan={self.tier.name})"
                )
        return True, ""

    def record(self, meter: str, now: float | None = None, count: int = 1) -> None:
        """Charge a meter. Charge on ATTEMPT, not on confirmed success: a send
        that landed but failed post-send verification must not go uncounted, or
        the ledger drifts low in the unsafe direction. Pair with `refund()` when
        the action provably did NOT happen."""
        stamps = self.state.meter(meter)
        at = time.time() if now is None else now
        for _ in range(max(0, count)):
            stamps.append(at)
            self._pending.append((meter, at))
            self._mine.append((meter, at))

    def refund(self, meter: str, count: int = 1) -> int:
        """Give back charges for actions that provably did NOT happen (e.g.
        LinkedIn's weekly-limit dialog appeared INSTEAD of the invite sending).

        Only events THIS pacer recorded are refundable — a refund must never
        strip another operation's history from the shared ledger. Works whether
        or not the attempt charge was already saved (charge → save → browser →
        proven no-send is the normal order). Returns how many were refunded."""
        refunded = 0
        for _ in range(max(0, count)):
            idx = next(
                (i for i in range(len(self._mine) - 1, -1, -1)
                 if self._mine[i][0] == meter),
                None,
            )
            if idx is None:
                break
            event = self._mine.pop(idx)
            if event in self._pending:
                self._pending.remove(event)  # never written — just drop it
            else:
                self._refunds.append(event)  # written — remove at next save
            with contextlib.suppress(ValueError):
                self.state.meter(meter).remove(event[1])
            refunded += 1
        return refunded

    def saturate(self, meter: str, now: float | None = None) -> None:
        """Mark a meter observed-exhausted: LinkedIn itself refused the action
        (e.g. the free-plan notes upsell dialog), which is ground truth that no
        allowance remains whatever our estimate said. Fills every budgeted
        window so `check()` refuses until the window rolls."""
        now = time.time() if now is None else now
        u = self.usage(meter, now)
        needed = max(
            (u[f"{w}_remaining"] for w in ("day", "week", "month")
             if f"{w}_remaining" in u),
            default=0,
        )
        if needed:
            self.record(meter, now=now, count=int(needed))

    def remaining(self, now: float | None = None) -> dict:
        """The live quota the host displays and gates the popup on.

        The invite keys are the original flat shape (`daily_cap`, `weekly_used`,
        …) so the app-side DTO and the typed facade keep working unchanged; the
        other meters are nested under `meters`.
        """
        now = time.time() if now is None else now
        inv = self.usage("invites", now)
        daily_remaining = inv["day_remaining"] or 0
        weekly_remaining = inv["week_remaining"] or 0
        return {
            "tier": self.tier.name,
            "daily_cap": inv["day_cap"],
            "weekly_cap": inv["week_cap"],
            "daily_used": inv["day_used"],
            "weekly_used": inv["week_used"],
            "daily_remaining": daily_remaining,
            "weekly_remaining": weekly_remaining,
            # 1st-degree DMs are now capped too (they were tracked-but-uncapped,
            # which combined with an unbounded reach-out list meant one click
            # could enqueue arbitrarily many real messages). Keys kept for the
            # existing quota view.
            "dm_daily_sent": _count_within(self.state.dms, DAY_SECONDS, now),
            "dm_weekly_sent": _count_within(self.state.dms, WEEK_SECONDS, now),
            "invites_available": min(daily_remaining, weekly_remaining),
            "meters": {m: self.usage(m, now) for m in METER_WINDOWS},
            "paused": self.is_paused(now),
            "paused_until": self.state.paused_until,
            "paused_reason": self.state.paused_reason,
        }

    def can_send_invite(self, now: float | None = None) -> tuple[bool, str]:
        return self.check("invites", now)

    def can_send_dm(self, now: float | None = None) -> tuple[bool, str]:
        return self.check("dms", now)

    def can_view_profile(self, now: float | None = None) -> tuple[bool, str]:
        return self.check("profile_views", now)

    def can_search(self, now: float | None = None) -> tuple[bool, str]:
        """CUL-counted search (People / company results). **Job search is exempt**
        per LinkedIn help `a564226`, so it must NOT call this — metering it here
        would throttle the product's primary use case for zero real risk."""
        return self.check("searches", now)

    def can_use_note(self, now: float | None = None) -> tuple[bool, str]:
        return self.check("notes", now)

    def can_search_jobs(self, now: float | None = None) -> tuple[bool, str]:
        """The self-imposed pages/hour throttle on logged-in job search. This is
        OURS, not LinkedIn's — job search is CUL-exempt (help `a564226`) — so it
        does NOT charge the `searches` (People-search CUL) meter. It only paces
        the authenticated page fetches so one sitting can't fan a role list into
        a large burst. A membership with no hourly ceiling → always allowed."""
        if getattr(self.tier.job_search_pages, "hour", None) is None:
            return True, ""
        return self.check("job_search_pages", now)

    def record_invite(self, now: float | None = None) -> None:
        self.record("invites", now)

    def record_dm(self, now: float | None = None) -> None:
        self.record("dms", now)

    def record_profile_view(self, now: float | None = None, count: int = 1) -> None:
        self.record("profile_views", now, count)

    def record_search(self, now: float | None = None) -> None:
        self.record("searches", now)

    def record_note(self, now: float | None = None) -> None:
        self.record("notes", now)

    def record_search_page(self, now: float | None = None) -> None:
        """Charge one logged-in job-search page fetch against the pages/hour
        throttle. No-op when the membership has no hourly ceiling."""
        if getattr(self.tier.job_search_pages, "hour", None) is not None:
            self.record("job_search_pages", now)

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
