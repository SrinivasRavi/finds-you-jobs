"""First-run seeding (ROADMAP A4, architecture section 7).

On a fresh DB: seed `UserPreferences.portals_config` from the shipped verified
source registry (`scraper/registry/portals-all.toml`) so the user has an
out-of-box source set they can trim, and create the recurring `scan` /
`score_new` schedules.

The schedules are seeded **disabled** on purpose: an unattended full-registry
scan (315 boards) followed by scoring every discovered job would spend real LLM
budget with no user in the loop, and budget *enforcement* is a G7 item (usage is
recorded, never enforced — ROADMAP section 4). Onboarding/Settings flips them on with
the user's chosen cadence + batch cap. Idempotent — safe to call every boot.
"""

from __future__ import annotations

import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any

from .db import Database
from .db.base import now_utc
from .logging_setup import get_logger

_REGISTRY_TOML = (
    Path(__file__).resolve().parent.parent
    / "modules"
    / "scraper"
    / "registry"
    / "portals-all.toml"
)


def _default_portals() -> dict[str, Any]:
    if not _REGISTRY_TOML.exists():
        return {}
    try:
        return tomllib.loads(_REGISTRY_TOML.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        get_logger().warning("seed: could not read %s", _REGISTRY_TOML)
        return {}


def seed_defaults(db: Database) -> None:
    """Seed portals config + schedules if absent (idempotent)."""
    with db.repos() as repos:
        prefs = repos.preferences.get_or_create()
        if not prefs.portals_config:
            portals = _default_portals()
            if portals:
                prefs.portals_config = portals
                get_logger().info(
                    "seed: portals_config seeded (%d sources)",
                    len(portals.get("sources", [])),
                )

        existing = {s.kind for s in repos.schedules.list_all()}
        far_future = now_utc() + timedelta(days=3650)  # effectively "never" until enabled
        if "scan" not in existing:
            repos.schedules.create("scan", 1440, next_due_at=far_future, enabled=False)
        if "score_new" not in existing:
            repos.schedules.create("score_new", 60, next_due_at=far_future, enabled=False)
        # US-NW-11 / FR-NW-13: auto-archive never-accepted connections after 60
        # days. Zero-LLM, zero-network, non-destructive → seeded **enabled** (unlike
        # the budget-spending scan/score) so the kanban self-prunes without the
        # user having to. Daily cadence; first run one day out.
        if "archive_stale_contacts" not in existing:
            repos.schedules.create(
                "archive_stale_contacts", 1440,
                next_due_at=now_utc() + timedelta(days=1), enabled=True,
            )
        # FR-SYS-04 / FR-JB-12: age Trashed jobs out (tombstone) after 7 days.
        # Zero-LLM, zero-network, no LLM budget → seeded **enabled** (like
        # archive_stale_contacts) so Trash self-empties. Daily cadence.
        if "cleanup_trash" not in existing:
            repos.schedules.create(
                "cleanup_trash", 1440,
                next_due_at=now_utc() + timedelta(days=1), enabled=True,
            )
        # US-NW-12 / FR-NW-15: contact-status sync is NO LONGER SCHEDULED.
        #
        # It used to be seeded **enabled** on a 12 h cadence, on the reasoning
        # that the entrypoint no-ops while Referral Outreach is off. That gate is
        # real, but once a user opts in it made the app touch LinkedIn on a timer
        # with nobody present — an unattended background daemon against LinkedIn,
        # which is the single hardest thing to defend and the one fact that broke
        # the "every LinkedIn action is user-initiated" claim
        # (`docs/internal/linkedin-addon.md` section 5, maintainer directive
        # 2026-07-30). Refreshing is now something the user asks for: an explicit
        # Sync button, plus an opportunistic refresh when they open the Networking
        # surface (throttled — see CONTACT_SYNC_MIN_INTERVAL_MINUTES).
        #
        # Existing installs carry an enabled row from before this change, so
        # retire it here rather than leaving a timer running after an update.
        stale_sync = next((s for s in repos.schedules.list_all() if s.kind == "contact_sync"), None)
        if stale_sync is not None and stale_sync.enabled:
            repos.schedules.update(stale_sync.id, enabled=False)
            get_logger().info("seed: retired the scheduled contact_sync (user-initiated only now)")
