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
    # How this card entered the pipeline (FR-TR — manual-add):
    # `discovered` = created through the fyj flow (scan / add-by-URL → Save);
    # `manual` = logged by the user via "Add a job application" for a job they
    # already applied to outside the app. Drives the Tracker source filter.
    origin: Mapped[str] = mapped_column(String, nullable=False, default="discovered")
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


class Document(Base):
    """A content-addressed uploaded file (a resume/cover letter the user
    actually submitted for a manually-logged application, FR-TR manual-add).

    The blob is stored once on disk at `<data_dir>/documents/<sha256>`; this row
    is its index + dedup key. Re-uploading identical bytes resolves to the SAME
    row (the `sha256` unique constraint), so no duplicate storage. Rows are
    referenced from `application_documents` — one blob may back many links."""

    __tablename__ = "documents"

    id: Mapped[str] = _pk()
    # SHA-256 hex of the raw bytes — the dedup key AND the on-disk filename.
    sha256: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(String, nullable=False, default="")
    original_filename: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)


class ApplicationDocument(Base):
    """Links an uploaded `Document` to an application as its resume or cover
    letter (FR-TR manual-add). `kind` mirrors the artifact vocabulary
    (`tailored_resume` | `cover_letter`) so the Tracker card can slot it beside
    the generated variants. One document per (application, kind)."""

    __tablename__ = "application_documents"

    id: Mapped[str] = _pk()
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        String, ForeignKey("documents.id"), nullable=False
    )
    # tailored_resume | cover_letter
    kind: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)

    __table_args__ = (
        UniqueConstraint("application_id", "kind", name="uq_appdoc_kind"),
        Index("ix_appdoc_application", "application_id"),
    )


class ApplyRun(Base):
    """One durable Applier attempt (`docs/internal/applier.md` §9.1) — the
    first-class replacement for the prior repository's `applications.apply_state`
    overload. A retry/reopen NEVER mutates an old run: it creates a fresh row
    linked by `retry_of_run_id`, and the old run stays immutable evidence
    (§8.3). P1 terminal statuses come from the jobapplier package — there is
    no `submitted` written by the agent; Applied requires confirmation
    evidence or explicit user attestation (§8.4), recorded by the API layer."""

    __tablename__ = "apply_runs"

    id: Mapped[str] = _pk()
    application_id: Mapped[str] = mapped_column(
        String, ForeignKey("applications.id"), nullable=False
    )
    operation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_of_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # waiting_for_packet | running | ready_for_human | blocked | timed_out |
    # interrupted | failed | submitted (API-side, evidence/attested only)
    status: Mapped[str] = mapped_column(String, nullable=False, default="waiting_for_packet")
    phase: Mapped[str] = mapped_column(String, nullable=False, default="waiting_for_packet")
    source_url: Mapped[str] = mapped_column(String, nullable=False, default="")
    final_url: Mapped[str] = mapped_column(String, nullable=False, default="")
    resume_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_artifact_id: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Redacted JSON evidence (labels/kinds/paths — never raw form values §9.1):
    # blockers [{kind, detail, field_label}], fields [{label, action, ok, note}],
    # screenshots [paths], usage {calls, tokens_in, tokens_out, cost_usd}.
    blockers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    screenshots: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # submitted-evidence trail (§8.4): none | confirmation_detected | user_attested
    submit_evidence: Mapped[str] = mapped_column(String, nullable=False, default="none")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_applyrun_application", "application_id", "started_at"),)


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


# ---------------------------------------------------------------------------
# Networking (database-design §5)
# ---------------------------------------------------------------------------


class Contact(Base):
    """One person discovered as (or added as) an outreach target (US-REF-05).

    Person-level, not job-level: the same person is reused across every role at
    their company (US-REF-04 "one connection per contact, ever"). `linkedin_url`
    is the identity key (same person + new URL = new row in P1 — database-design
    §8 Q4). `audience_tag`/`warmth` are auto-assigned at discovery (US-REF-02/10);
    `connection_status` is the kanban column (US-NW-07)."""

    __tablename__ = "contacts"

    id: Mapped[str] = _pk()
    linkedin_url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    current_role: Mapped[str] = mapped_column(String, nullable=False, default="")
    current_company: Mapped[str] = mapped_column(String, nullable=False, default="")
    headline: Mapped[str] = mapped_column(String, nullable=False, default="")
    connection_degree: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_first_degree: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # peer / hm / recruiter / leadership / other (US-NW-09 / US-REF-02).
    audience_tag: Mapped[str] = mapped_column(String, nullable=False, default="other")
    warmth: Mapped[str] = mapped_column(String, nullable=False, default="cold")  # warm | cold
    # Lifecycle. `candidate` = discovered but not yet reached (off the kanban);
    # the kanban columns are sent | accepted | engagement | ghosted | converted
    # (US-NW-01). Manual add-by-URL sets one of the live columns directly.
    connection_status: Mapped[str] = mapped_column(String, nullable=False, default="candidate")
    profile_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)
    last_touched_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=now_utc, onupdate=now_utc
    )
    # Sent-timestamp clock for the 60-day never-accepted auto-archive (US-NW-11).
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        Index("ix_contacts_company", "current_company"),
        Index("ix_contacts_status", "connection_status"),
    )


class CompanyResolution(Base):
    """A cached name → LinkedIn company-entity resolution (FR-NW-02).

    Keyed by a stable per-employer key (`domain:…` > `<adapter>:<slug>` >
    `name:…`, see `registry/company_anchor.py`) so every job of the same employer
    reuses ONE typeahead call + ONE user confirm choice — no re-prompting, no
    inconsistent picks across a company's jobs. `source` records how the URN was
    chosen: `domain` (silent — the employer website matched), `single` (the only
    candidate), or `user` (confirmed in the find-referrals popup)."""

    __tablename__ = "company_resolutions"

    id: Mapped[str] = _pk()
    resolution_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    company_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    company_urn: Mapped[str] = mapped_column(String, nullable=False, default="")
    company_vanity: Mapped[str] = mapped_column(String, nullable=False, default="")
    industry: Mapped[str] = mapped_column(String, nullable=False, default="")
    source: Mapped[str] = mapped_column(String, nullable=False, default="user")
    resolved_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=now_utc, onupdate=now_utc
    )


class ContactJobAssoc(Base):
    """A contact ↔ job link: one referral ask per role (US-REF-05).

    The per-role ask status is distinct from the person-level connection status
    on `Contact` (a contact reused across roles has one connection but many asks).
    `audience_tag` is copied here at add-time so a job-scoped view keeps its own
    tag even if the person's role later changes."""

    __tablename__ = "contact_job_assocs"

    id: Mapped[str] = _pk()
    contact_id: Mapped[str] = mapped_column(
        String, ForeignKey("contacts.id"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False)
    audience_tag: Mapped[str] = mapped_column(String, nullable=False, default="other")
    # pending / accepted / replied / converted / ignored (database-design §5).
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # The user's current find-referrals selection for this role (FR-NW-01). Set at
    # reach-out time; persisted so a `pending` popup restores the selection on
    # reopen (candidates + who was picked) after a partial/cap-stopped batch.
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)

    __table_args__ = (
        UniqueConstraint("contact_id", "job_id", name="uq_contactjob"),
        Index("ix_contactjob_job", "job_id"),
    )


class Sequence(Base):
    """An audience playbook (US-PLB-*): the ordered outreach steps for one
    audience. P1 seeds these from the Networker's bundled playbook files; the
    editable Playbook Editor (US-PLB-01..05) writes them back. `is_default`
    marks a canonical seeded playbook (Reset-to-default target)."""

    __tablename__ = "sequences"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    # peer | hm | recruiter | leadership | other
    audience: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)

    steps: Mapped[list[SequenceStep]] = relationship(
        back_populates="sequence", cascade="all, delete-orphan"
    )


class SequenceStep(Base):
    """One step of a playbook (US-PLB-02/03/05)."""

    __tablename__ = "sequence_steps"

    id: Mapped[str] = _pk()
    sequence_id: Mapped[str] = mapped_column(
        String, ForeignKey("sequences.id"), nullable=False
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String, nullable=False, default="")
    # linkedin_connect / linkedin_dm / email (email = manual-send-only P1, §17a).
    channel: Mapped[str] = mapped_column(String, nullable=False, default="linkedin_dm")
    body_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    delay_days_from_previous: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # manual | after_previous_days
    trigger: Mapped[str] = mapped_column(String, nullable=False, default="manual")

    sequence: Mapped[Sequence] = relationship(back_populates="steps")

    __table_args__ = (Index("ix_seqstep_sequence", "sequence_id", "order_index"),)


class OutreachLog(Base):
    """Per-message audit (database-design §5). One row per send attempt.

    `outcome_detail` carries the verbatim underlying error on failure
    (NFR-SIDE-04, never swallowed). Cap/quota accounting is NOT stored here — the
    Referral Outreach package owns the caps; the app queries live quota (§17c)."""

    __tablename__ = "outreach_logs"

    id: Mapped[str] = _pk()
    contact_id: Mapped[str] = mapped_column(
        String, ForeignKey("contacts.id"), nullable=False
    )
    job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.id"), nullable=True)
    sequence_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("sequences.id"), nullable=True
    )
    step_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("sequence_steps.id"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)  # connection_note | dm
    # The reach-out batch this send belongs to (FR-NW-01/03). One batch id ties
    # every send of a single "Reach out (N)" together, so `referralsState` derives
    # from the latest batch (all members sent → reachedOut, some → pending, none →
    # failed). Null for legacy/manual single sends (each is its own settled batch).
    batch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    body_sent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # sent | failed | pending
    outcome: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    outcome_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    operation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("operations.id"), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=now_utc)

    __table_args__ = (
        Index("ix_outreach_contact", "contact_id"),
        Index("ix_outreach_job", "job_id"),
    )


class LinkedInSession(Base):
    """Single-row LinkedIn session state (database-design §5).

    `cookies_encrypted` is a secret-at-rest BLOB (NFR-SEC-01 / §6) — never
    plaintext. `status` gates the popup's send path (US-NW-09): a `valid` session
    unlocks live discover/send; anything else is drafts-only / manual-web."""

    __tablename__ = "linkedin_sessions"

    id: Mapped[str] = _pk()
    cookies_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # valid | expired | never_set | connecting | backing_off (N4). `connecting`
    # while a headed `login` op is in flight; `backing_off` after the Referral
    # Outreach package reports a rate-limit pause (FR-NW-05) — cleared by resume.
    status: Mapped[str] = mapped_column(String, nullable=False, default="never_set")
    account_tier: Mapped[str] = mapped_column(String, nullable=False, default="new")  # new|seasoned
    # N4 session-capture metadata for Settings → LinkedIn session (US-SET-06).
    # `connected_as` is the member's display name (best-effort, DOM-read at login);
    # `li_at_expires_at` drives the expiry pill; `last_validated_at` is the local
    # revalidation clock. `paused_reason` carries the verbatim backoff text.
    connected_as: Mapped[str] = mapped_column(String, nullable=False, default="")
    li_at_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    paused_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    paused_reason: Mapped[str] = mapped_column(String, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=now_utc, onupdate=now_utc
    )
