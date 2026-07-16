"""SQLAlchemy 2.0 models — the core-storage slice of the first-migration schema
(database-design §7, carried per `docs/internal/roadmap.md` §7.2 #3).

This commit carries orchestration only: `operations` (the runner's durable
queue + cost ledger) and `user_preferences` (whose `ui_state["cost_totals"]`
aggregate keeps all-time spend intact across ledger retention). The remaining
§7 tables (jobs, applications, profile, artifacts, …) land with their feature
commits as follow-up migrations. Enum-valued columns are plain TEXT (string
enums — "new kinds need no migration", §2); JSON columns use SQLAlchemy's
cross-dialect JSON type.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UTCDateTime, now_utc
from .ids import uuid7


def _pk() -> Mapped[str]:
    return mapped_column(String, primary_key=True, default=uuid7)


# ---------------------------------------------------------------------------
# Orchestration (database-design §2)
# ---------------------------------------------------------------------------


class Operation(Base):
    """The runner's durable queue row + the cost ledger (architecture §5.3)."""

    __tablename__ = "operations"

    id: Mapped[str] = _pk()
    kind: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    engine: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        Index("ix_operations_state_created", "state", "created_at"),
        Index("ix_operations_kind_created", "kind", "created_at"),
        Index("ix_operations_finished", "finished_at"),
    )


class UserPreferences(Base):
    """Filters + constraints; single row in P1 (database-design §4)."""

    __tablename__ = "user_preferences"

    id: Mapped[str] = _pk()
    role_aliases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    locations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    freshness_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hard_excludes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    hard_requires: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    soft_preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    thresholds: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    portals_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    voyager_risk_marker_on: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    engine_routing: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ui_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
