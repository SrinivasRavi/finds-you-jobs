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

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class Schedule(Base):
    """A recurring operation (database-design §2)."""

    __tablename__ = "schedules"

    id: Mapped[str] = _pk()
    kind: Mapped[str] = mapped_column(String, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    next_due_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)
    last_enqueued_operation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("operations.id"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ---------------------------------------------------------------------------
# Jobs (database-design §3)
# ---------------------------------------------------------------------------


class Job(Base):
    """One discovered posting; `canonical_url` is the dedup key (FR-SYS-01)."""

    __tablename__ = "jobs"

    id: Mapped[str] = _pk()
    canonical_url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str] = mapped_column(String, nullable=False, default="")
    location: Mapped[str] = mapped_column(String, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    posted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    salary: Mapped[str | None] = mapped_column(String, nullable=True)
    source_adapter: Mapped[str] = mapped_column(String, nullable=False)
    trust_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    trust_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)
    feed_state: Mapped[str] = mapped_column(String, nullable=False, default="active")
    source_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_jobs_feedstate_ingested", "feed_state", "ingested_at"),
        Index("ix_jobs_company", "company"),
    )


class JobScore(Base):
    """The cached fit score for one `(job, profile_version, scorer_impl)`."""

    __tablename__ = "job_scores"

    id: Mapped[str] = _pk()
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    score_0_100: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    breakdown_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scorer_impl: Mapped[str] = mapped_column(String, nullable=False, default="scorer-llm")
    operation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("operations.id"), nullable=True
    )
    scored_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)

    __table_args__ = (
        UniqueConstraint("job_id", "profile_version", "scorer_impl", name="uq_jobscore_cachekey"),
    )


class Tombstone(Base):
    """A permanently-discarded canonical URL — never re-ingested (FR-SYS-04)."""

    __tablename__ = "tombstones"

    id: Mapped[str] = _pk()
    canonical_url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    tombstoned_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="manual")


# ---------------------------------------------------------------------------
# Profile (database-design §3)
# ---------------------------------------------------------------------------


class MasterProfile(Base):
    __tablename__ = "master_profiles"

    id: Mapped[str] = _pk()
    resume_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Structured form-fill facts (FR-APP-01): extracted from the resume by the
    # `extract` op at save, user-editable in Settings; the Applier reads this
    # instead of regex-scraping the markdown. Nullable — absent means not yet
    # extracted.
    application_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=now_utc, onupdate=now_utc
    )

    entities: Mapped[list[ProfileEntity]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


# Join tables per the no-graph-DB decision (§4): entity↔entity links live in SQL.
experience_skills = Table(
    "experience_skills",
    Base.metadata,
    Column("experience_id", String, ForeignKey("profile_entities.id"), primary_key=True),
    Column("skill_id", String, ForeignKey("profile_entities.id"), primary_key=True),
)

project_skills = Table(
    "project_skills",
    Base.metadata,
    Column("project_id", String, ForeignKey("profile_entities.id"), primary_key=True),
    Column("skill_id", String, ForeignKey("profile_entities.id"), primary_key=True),
)


class ProfileEntity(Base):
    """Extracted profile entity backing the FR-TL-01 fabrication guard."""

    __tablename__ = "profile_entities"

    id: Mapped[str] = _pk()
    profile_id: Mapped[str] = mapped_column(
        String, ForeignKey("master_profiles.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_curated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    profile: Mapped[MasterProfile] = relationship(back_populates="entities")


# ---------------------------------------------------------------------------
# Applications (database-design §4)
# ---------------------------------------------------------------------------


class Application(Base):
    """A pipeline card (database-design §4). `packetState` is *not* stored here —
    it's derived from this app's Artifact rows + their operations' states.

    The prior repository also stored `apply_state` (latest Applier run summary)
    and `form_prep` (the Save-time answer cache) here; both are retired in this
    rebuild (`docs/internal/applier.md` §2) — the durable ApplyRun model arrives
    with the applier commits instead."""

    __tablename__ = "applications"

    id: Mapped[str] = _pk()
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    column: Mapped[str] = mapped_column(String, nullable=False, default="saved")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="P0")
    # Exclusive pre-submission intent (`docs/internal/roadmap.md` §5.1):
    # `none | referral | apply` — one authoritative value, so two competing
    # background paths can never present conflicting calls to action.
    intent: Mapped[str] = mapped_column(String, nullable=False, default="none")
    notes_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    applied_via: Mapped[str | None] = mapped_column(String, nullable=True)
    preview_screenshot_path: Mapped[str | None] = mapped_column(String, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    saved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)
    last_touched_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=now_utc, onupdate=now_utc
    )

    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class Artifact(Base):
    """One generated document (tailored resume | cover letter) — database-design §4.
    Resume + cover letter are two separate operations → two rows (AM5)."""

    __tablename__ = "artifacts"

    id: Mapped[str] = _pk()
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    guidance_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("operations.id"), nullable=True
    )
    superseded_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("artifacts.id"), nullable=True
    )
    # "Approve and Save" stamp (US-RES-02 / FR-RES-02). Null → the variant is at
    # `ready` (or generating/failed/none); set → the per-kind `approved` state.
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)

    application: Mapped[Application] = relationship(back_populates="artifacts")


class ApplicationEvent(Base):
    """A user-driven lifecycle event on a card (FR-TR-03/04) — the Activity-tab
    source for the actions the operations ledger never sees: a column move
    (`detail={"from","to"}`), a notes edit (`kind="notes"`), and archive /
    unarchive. Composed with the ledger in `GET …/activity`. Kinds are plain
    TEXT string-enums (§2). No other kinds are written."""

    __tablename__ = "application_events"

    id: Mapped[str] = _pk()
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id"), nullable=False
    )
    # column_change | notes | archive | unarchive
    kind: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)

    __table_args__ = (Index("ix_appevent_application", "application_id", "created_at"),)


# ---------------------------------------------------------------------------
# Settings (database-design §4/§6)
# ---------------------------------------------------------------------------


class EngineSettings(Base):
    """One row per configured LLM engine (architecture §9 registry)."""

    __tablename__ = "engine_settings"

    id: Mapped[str] = _pk()
    engine: Mapped[str] = mapped_column(String, nullable=False)
    key_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    # Encrypted-at-rest — never plaintext (NFR-SEC-01).
    key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    default_model: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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
