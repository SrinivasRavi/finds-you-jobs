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
    # Contact kanban ghosting (FR-NW-15). Engagement threads go quiet → Ghosted;
    # a separate, longer window covers Sent/Accepted-but-never-replied stalls.
    "engagement_ghosted_days": 14,
    "sent_ghosted_days": 21,
    # Permanent purge of soft-deleted (archived) rows.
    "contact_purge_days": 60,           # deleted (archived) contacts
    "trashed_jobs_purge_days": 7,       # trashed jobs (was TRASH_TTL_DAYS)
    "archived_applications_purge_days": 30,  # archived tracker cards (was: never)
    # Contact-status sync cadence (hours). Also mirrored to the schedule row.
    "contact_sync_cadence_hours": 12,
}

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
