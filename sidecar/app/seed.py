"""First-run seeding (ROADMAP A4, architecture §7).

On a fresh DB: seed `UserPreferences.portals_config` from the shipped verified
source registry (`scraper/registry/portals-all.toml`) so the user has an
out-of-box source set they can trim, and create the recurring `scan` /
`score_new` schedules.

The schedules are seeded **disabled** on purpose: an unattended full-registry
scan (315 boards) followed by scoring every discovered job would spend real LLM
budget with no user in the loop, and budget *enforcement* is a G7 item (usage is
recorded, never enforced — ROADMAP §4). Onboarding/Settings flips them on with
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
        # US-NW-12 / FR-NW-15: periodic LinkedIn contact-status sync. Seeded
        # **enabled** but the entrypoint no-ops cleanly when Referral Outreach is
        # OFF or the session is disconnected (zero LinkedIn traffic until the user
        # opts in), so it's safe on by default. Modest 12 h cadence (720 min);
        # user-adjustable in Settings → Contact & data lifecycle. First run 1 h out.
        if "contact_sync" not in existing:
            from .lifecycle import LIFECYCLE_DEFAULTS

            repos.schedules.create(
                "contact_sync",
                LIFECYCLE_DEFAULTS["contact_sync_cadence_hours"] * 60,
                next_due_at=now_utc() + timedelta(hours=1), enabled=True,
            )
