"""Configurable entity-lifecycle windows (US-SYS-06 / FR-SYS-06, 2026-07-15).

Every auto-lifecycle timer in the app — contact kanban ghosting, deleted-contact
/ trashed-job / archived-application purge, and the contact-sync cadence — reads
its window from here instead of a hard-coded constant, so the user owns them from
Settings (maintainer directive 2026-07-15: "keep ALL entities' lifecycle
user-configurable, including deleted jobs and applications").

Home: `UserPreferences.ui_state["lifecycle"]` — a JSON sub-dict (no schema
migration; `ui_state` is the established settings bag that already holds the OTLP
/ retention config). `resolve_lifecycle(prefs)` merges the stored values over
`LIFECYCLE_DEFAULTS`; the defaults preserve the pre-2026-07-15 behavior (trashed
jobs still purge at 7 days, etc.) so nothing changes for a user who never opens
the new Settings section.
"""

from __future__ import annotations

from typing import Any

# Defaults chosen to (a) preserve existing behavior where a constant already
# existed (trashed jobs = the old TRASH_TTL_DAYS=7) and (b) be sensible bootstraps
# where none did. All are whole days except the sync cadence (hours).
LIFECYCLE_DEFAULTS: dict[str, int] = {
    # NOTE: `contact_sync_cadence_hours` is gone — contact sync is user-initiated
    # only (no schedule to retime; see CONTACT_SYNC_MIN_INTERVAL_MINUTES below).
    # Contact kanban ghosting (FR-NW-15). Engagement threads go quiet → Ghosted;
    # a separate, longer window covers Sent/Accepted-but-never-replied stalls.
    "engagement_ghosted_days": 14,
    "sent_ghosted_days": 21,
    # Feed aging (FR-SYS-03): a job that has sat on the board this many days
    # (from first ingest; a Restore resets the clock via `feed_since`) is greyed
    # as an "Older listing" — still on the board and restorable; hard-deleted ~30
    # days after greying unless Saved. NOTE the clock is board-age, NOT
    # "not re-seen in a scan" — dedupe is first-seen-wins and refreshes nothing.
    # Was the hard-coded `persistence.EXPIRE_AFTER_DAYS=14`; now user-owned.
    "expire_listing_days": 14,
    # Permanent purge of soft-deleted (archived) rows.
    "contact_purge_days": 60,           # deleted (archived) contacts
    "trashed_jobs_purge_days": 7,       # trashed jobs (was TRASH_TTL_DAYS)
    "archived_applications_purge_days": 30,  # archived tracker cards (was: never)
}

# Contact sync is user-initiated only (no scheduled LinkedIn traffic — see
# `seed.py` and `docs/internal/linkedin-posture.md` §1). Opening the Networking
# surface may trigger an opportunistic refresh, but no more often than this. The
# explicit Sync button bypasses it: an on-demand refresh is the user asking, and
# is no more traffic than them opening linkedin.com themselves.
CONTACT_SYNC_MIN_INTERVAL_MINUTES = 15

# How recently a MANUAL kanban drag protects a contact from being auto-overridden
# (manual-wins). Not surfaced in the UI — a fixed guard so auto never immediately
# fights a fresh manual move (US-NW-12 acceptance). Days.
MANUAL_OVERRIDE_COOLDOWN_DAYS = 3

# The UI-editable keys (the Settings lifecycle section renders exactly these).
LIFECYCLE_KEYS: tuple[str, ...] = tuple(LIFECYCLE_DEFAULTS)


def resolve_lifecycle(prefs: Any) -> dict[str, int]:
    """Merge stored `ui_state["lifecycle"]` over the defaults → the effective
    windows. Non-int / non-positive stored values fall back to the default (a
    zero or garbage window must never silently disable a purge or ghost every
    contact instantly)."""
    ui_state = getattr(prefs, "ui_state", None) or {}
    stored = ui_state.get("lifecycle") if isinstance(ui_state, dict) else None
    merged = dict(LIFECYCLE_DEFAULTS)
    if isinstance(stored, dict):
        for key in LIFECYCLE_DEFAULTS:
            value = stored.get(key)
            if isinstance(value, bool):  # bool is an int subclass — reject it
                continue
            if isinstance(value, (int, float)) and value > 0:
                merged[key] = int(value)
    return merged
