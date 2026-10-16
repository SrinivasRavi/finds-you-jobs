"""First-run seeding (architecture section 7).

On a fresh DB: seed `portals_config` from the shipped source registry
(`scraper/registry/portals-all.toml`) and create the `scan` and `score_new`
schedules, disabled, so nothing spends LLM budget unattended until onboarding or
Settings turns them on. Idempotent; runs every boot."""

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
        # The autopilot tick (audit-2026-10-14.md section 8.10). Seeded enabled:
        # with every autopilot switch off, a tick reads settings and enqueues nothing.
        if "autopilot" not in existing:
            repos.schedules.create(
                "autopilot", 30,
                next_due_at=now_utc() + timedelta(minutes=30), enabled=True,
            )
        # US-NW-12 / FR-NW-15: contact sync is no longer scheduled. A 12 h timer touched
        # LinkedIn with nobody present, breaking "every LinkedIn action is user-initiated"
        # (linkedin-addon.md section 5). The Sync button is the only trigger. Existing
        # installs carry an enabled row, so retire it here.
        stale_sync = next((s for s in repos.schedules.list_all() if s.kind == "contact_sync"), None)
        if stale_sync is not None and stale_sync.enabled:
            repos.schedules.update(stale_sync.id, enabled=False)
            get_logger().info("seed: retired the scheduled contact_sync (user-initiated only now)")
