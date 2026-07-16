"""Pydantic DTOs — the HTTP boundary (architecture §4.2, §5.2 one-way rule).

DTO ↔ ORM conversion happens *here* and only here: models/dataclasses never
cross into the wire types, and Pydantic never leaks into `modules/`. These
shapes drive the OpenAPI → TS codegen, so drift is a build error.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..db.models import (
    Application,
    Artifact,
    EngineSettings,
    Job,
    JobScore,
    MasterProfile,
    Operation,
    Schedule,
    UserPreferences,
)

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class JobScoreDTO(BaseModel):
    """The cached fit score for the current master version (FR-JB-01 sort)."""

    score_0_100: int
    reasons: list[Any]
    breakdown_md: str


class JobDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    canonical_url: str
    title: str
    company: str
    location: str
    description: str
    posted_at: str | None
    salary: str | None
    source_adapter: str
    trust_score: int
    trust_flags: list[str]
    feed_state: str
    ingested_at: datetime
    # Best-effort work style (US-JB-01 row chip) derived app-side from the
    # location + description text — NOT part of the frozen NormalizedJob scraper
    # contract. `REMOTE` | `HYBRID` | `ONSITE`, or `""` when undeterminable (never
    # guessed). The same keyword logic backs FR-JB-04's work-style filter, so the
    # chip and the filter always agree.
    work_style: str = Field(default="", serialization_alias="workStyle")
    # The board serves jobs *with* their score (null while unscored).
    score: JobScoreDTO | None = None
    # Score lifecycle (FR-JB-07 / NFR-OFFLINE-02): `scored` (a real 0–100) /
    # `pending` (queued or not yet attempted) / `failed` (the score op errored and
    # none is in flight — the `Score failed` pill, never a perpetual spinner).
    score_status: str = Field(default="pending", serialization_alias="scoreStatus")


class BoardPageDTO(BaseModel):
    """One page of the Job Board feed (FR-JB-02) plus the header meta the board
    needs (FR-JB-10): a real last-scan time + scrape status so the empty state is
    always explained, never a silent blank. Saved jobs are excluded server-side."""

    jobs: list[JobDTO]
    total: int
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    # `running` (a scan is queued/in-flight) / `error` (last scan failed) /
    # `empty` (no eligible rows, last scan fine) / `idle` (rows present).
    scan_status: str = Field(serialization_alias="scanStatus")
    last_scan_at: datetime | None = Field(default=None, serialization_alias="lastScanAt")
    scan_error: str | None = Field(default=None, serialization_alias="scanError")


class JobCreate(BaseModel):
    """Add-by-URL (US-JB-07) + programmatic ingest."""

    canonical_url: str
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    posted_at: str | None = None
    salary: str | None = None
    source_adapter: str = "paste-url"


class JobPreviewRequest(BaseModel):
    """Add-by-URL step 1 (US-JB-07): fetch a pasted URL, extract fields, no persist."""

    url: str


class JobPreviewDTO(BaseModel):
    """The editable review payload the Add-by-URL modal shows before submit.

    Best-effort — a known ATS URL comes back fully structured; a generic page
    fills what it can and the user edits the rest."""

    canonical_url: str
    title: str
    company: str
    location: str
    description: str
    posted_at: str | None
    salary: str | None
    source_adapter: str


class JobUpdate(BaseModel):
    """App-side job state — Trash (US-JB-11): `feed_state` active/removed."""

    feed_state: str | None = None


class TombstoneResultDTO(BaseModel):
    """Result of a permanent-discard action (Empty Trash / Delete forever /
    TTL eviction — US-JB-11 / FR-SYS-04): how many URLs were tombstoned."""

    tombstoned: int
    canonical_urls: list[str]


class ScheduleDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    interval_minutes: int
    enabled: bool
    next_due_at: datetime
    last_enqueued_operation_id: str | None


class ScheduleUpdate(BaseModel):
    """Enable/disable a schedule or change its cadence."""

    enabled: bool | None = None
    interval_minutes: int | None = None


class ScheduleRunResult(BaseModel):
    """The `POST /api/schedules/{id}/run` response: the schedule + enqueued ops."""

    schedule: ScheduleDTO
    enqueued: list[str]


# ---------------------------------------------------------------------------
# Applications (database-design §4)
# ---------------------------------------------------------------------------


class ArtifactDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    application_id: str
    kind: str
    markdown: str
    notes: list[Any]
    profile_version: int
    guidance_used: str | None
    operation_id: str | None
    operation_state: str | None = None
    superseded_by: str | None
    approved_at: datetime | None = None
    created_at: datetime


class ApplicationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    job_id: str
    # The application's job, embedded server-side so the Tracker never joins
    # against a capped client-side jobs list. None only if the job row was
    # hard-deleted underneath.
    job: JobDTO | None = None
    column: str
    priority: str
    # Exclusive pre-submission intent (`docs/internal/roadmap.md` §5.1):
    # `none | referral | apply` — one authoritative value.
    intent: str = "none"
    notes_markdown: str
    applied_via: str | None
    preview_screenshot_path: str | None
    archived_at: datetime | None
    saved_at: datetime
    last_touched_at: datetime
    # Combined packet state (kept for the card-menu regen logic + Activity tab).
    packet_state: str = Field(serialization_alias="packetState")
    # Per-artifact states (US-RES-02 / US-CL-01 storage model): the Resume and
    # Cover-letter slots are driven independently — one generating/failing must
    # not repaint the other. `approved` comes from that artifact's approved_at.
    packet_resume_state: str = Field(
        default="none", serialization_alias="packetResumeState"
    )
    packet_cover_letter_state: str = Field(
        default="none", serialization_alias="packetCoverLetterState"
    )
    artifacts: list[ArtifactDTO] = Field(default_factory=list)


class ApplicationCreate(BaseModel):
    job_id: str
    column: str = "saved"
    # None → the server assigns priority by the Welford z-band at Save (FR-TR-09);
    # an explicit value is treated as a manual choice and used verbatim.
    priority: str | None = None
    notes_markdown: str = ""
    # Per-job automation toggles (US-TL-03). None → fall back to the split
    # auto-generate-on-Save settings (`thresholds.auto_{resume,cover}_on_save`).
    generate_resume: bool | None = None
    generate_cover: bool | None = None
    guidance: str = ""


class ApplicationUpdate(BaseModel):
    column: str | None = None
    priority: str | None = None
    # Exclusive intent (`docs/internal/roadmap.md` §5.1): setting one value
    # replaces the other — the column IS the single authoritative store.
    intent: Literal["none", "referral", "apply"] | None = None
    notes_markdown: str | None = None
    applied_via: str | None = None
    # True → archive (set archived_at), False → unarchive (clear it).
    archived: bool | None = None


class ArtifactPatch(BaseModel):
    """Persist an edited variant + the Approve-and-Save flip (US-RES-02 / FR-RES-02).

    `markdown` overwrites the head artifact's text; `approved=True` stamps
    `approved_at` (flip `ready → approved`), `approved=False` clears it."""

    markdown: str | None = None
    approved: bool | None = None


class PacketRequest(BaseModel):
    """Manual/regenerate packet build for an existing application (US-TL-02)."""

    resume: bool = True
    cover: bool = True
    guidance: str = ""


class ActivityEntryDTO(BaseModel):
    """One real event on an application's Activity tab (US-TR-03 / FR-TR-03),
    composed from the ledger — never synthesized client-side."""

    # added | score | tailor | cover (ledger) + column_change | notes |
    # archive | unarchive (user-driven card events).
    kind: str
    label: str
    state: str | None = None  # the backing op state, when applicable
    at: datetime | None = None


# ---------------------------------------------------------------------------
# Profile (database-design §3)
# ---------------------------------------------------------------------------


class ProfileDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resume_markdown: str
    version: int
    # Structured form-fill facts (FR-APP-01) — extracted by the `extract` op at
    # save, user-editable in Settings; null until extracted.
    application_profile: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ProfileUpsert(BaseModel):
    resume_markdown: str


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class PreferencesDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role_aliases: list[Any]
    locations: list[Any]
    freshness_days: int
    hard_excludes: dict[str, Any]
    hard_requires: dict[str, Any]
    soft_preferences: dict[str, Any]
    thresholds: dict[str, Any]
    portals_config: dict[str, Any]
    voyager_risk_marker_on: bool
    engine_routing: dict[str, Any]
    ui_state: dict[str, Any]


class PreferencesUpdate(BaseModel):
    role_aliases: list[Any] | None = None
    locations: list[Any] | None = None
    freshness_days: int | None = None
    hard_excludes: dict[str, Any] | None = None
    hard_requires: dict[str, Any] | None = None
    soft_preferences: dict[str, Any] | None = None
    thresholds: dict[str, Any] | None = None
    portals_config: dict[str, Any] | None = None
    voyager_risk_marker_on: bool | None = None
    engine_routing: dict[str, Any] | None = None
    ui_state: dict[str, Any] | None = None


class EngineSettingDTO(BaseModel):
    """Engine config sans secret — `has_key` reports presence, `key_hint` is a
    masked display fragment (e.g. `sk-…abc4`), never the key itself."""

    id: str
    engine: str
    base_url: str | None
    default_model: str | None
    enabled: bool
    has_key: bool
    key_hint: str | None


class SettingsDTO(BaseModel):
    preferences: PreferencesDTO
    engines: list[EngineSettingDTO]


class EngineVerifyRequest(BaseModel):
    """A provider-appropriate verify probe (FR-SET-06). `key` is sent for a
    verify-only check and is never persisted by this call."""

    provider: str
    key: str | None = None
    base_url: str | None = None
    model: str | None = None


class EngineVerifyResult(BaseModel):
    ok: bool
    detail: str
    provider: str
    # Onboarding branches on this: `not_found` (install the CLI) vs
    # `not_logged_in` (open a terminal, log in) vs `error` (show detail). Only
    # `claude-cli` uses the non-`ok`/`error` values; every other provider maps
    # ok→"ok" / not-ok→"error".
    status: Literal["ok", "not_found", "not_logged_in", "error"] = "ok"


class EngineSettingUpsert(BaseModel):
    """Save/replace a provider's config. Omitting `key` leaves any existing
    sealed key intact; sending `key` re-seals. The key never round-trips back."""

    provider: str
    key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    enabled: bool = True


# ---------------------------------------------------------------------------
# Operations (architecture §5.3 — the ledger surface)
# ---------------------------------------------------------------------------


class OperationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    state: str
    input_snapshot: dict[str, Any]
    result_ref: dict[str, Any] | None
    usage: dict[str, Any] | None
    error: str | None
    engine: str | None
    model: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class OperationAccepted(BaseModel):
    id: str
    kind: str
    state: str


class CostTotalsDTO(BaseModel):
    """All-time cost totals for the Analytics cost tiles (FR-SET-07 / US-LOG-01 #2).

    Live-ledger sum + the pruned-ops aggregate, so the figures are lifetime totals
    that survive ledger retention — not just the retained ~250 ops. `by_kind` maps
    each operation kind to its all-time usd spend."""

    usd: float
    tokens_in: int
    tokens_out: int
    operations: int
    failed: int
    by_kind: dict[str, float]


def job_score_dto(score: JobScore | None) -> JobScoreDTO | None:
    if score is None:
        return None
    return JobScoreDTO(
        score_0_100=score.score_0_100,
        reasons=list(score.reasons),
        breakdown_md=score.breakdown_md,
    )


def derive_score_status(has_score: bool, op_states: set[str]) -> str:
    """The board's Score lifecycle (FR-JB-07 / NFR-OFFLINE-02): a cached score
    wins; else a queued/running score op means Pending; else a failed op with no
    score means `Score failed`; else Pending (not yet attempted)."""
    if has_score:
        return "scored"
    if "queued" in op_states or "running" in op_states:
        return "pending"
    if "failed" in op_states:
        return "failed"
    return "pending"


_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_REMOTE_RE = re.compile(
    r"\bremote\b|\bwork from home\b|\bwfh\b|\bremote[- ]first\b|\bfully remote\b",
    re.IGNORECASE,
)
_ONSITE_RE = re.compile(r"\bon[- ]?site\b|\bin[- ]?office\b|\bin person\b", re.IGNORECASE)


def derive_work_style(location: str, description: str) -> str:
    """Best-effort work style from a job's location + description (US-JB-01 chip /
    FR-JB-04 filter — one source so the two agree). Keyword signals, precedence
    Hybrid → Remote → Onsite (an explicit "hybrid" outranks a stray "remote").
    Returns `""` when nothing matches — we never guess a style."""
    text = f"{location}\n{description}"
    if _HYBRID_RE.search(text):
        return "HYBRID"
    if _REMOTE_RE.search(text):
        return "REMOTE"
    if _ONSITE_RE.search(text):
        return "ONSITE"
    return ""


def job_dto(
    job: Job, score: JobScore | None = None, *, score_op_states: set[str] | None = None
) -> JobDTO:
    dto = JobDTO.model_validate(job)
    dto.score = job_score_dto(score)
    dto.score_status = derive_score_status(score is not None, score_op_states or set())
    dto.work_style = derive_work_style(job.location, job.description)
    return dto


def derive_packet_state(operation_states: list[str | None]) -> str:
    """The card's packetState from its artifacts' operation states (database-design §4).

    none → no artifacts yet; generating → any op still queued/running;
    failed → an op failed and none is generating; ready → everything settled.
    """
    if not operation_states:
        return "none"
    if any(state in ("queued", "running") for state in operation_states):
        return "generating"
    if any(state == "failed" for state in operation_states):
        return "failed"
    return "ready"


def derive_artifact_state(
    artifact: Artifact | None, operation_state: str | None
) -> str:
    """One slot's state (US-RES-02 table): none → no head artifact of this kind;
    generating → its op is queued/running; failed → its op failed; approved →
    approved_at is stamped; ready → otherwise settled."""
    if artifact is None:
        return "none"
    if operation_state in ("queued", "running"):
        return "generating"
    if operation_state == "failed":
        return "failed"
    if artifact.approved_at is not None:
        return "approved"
    return "ready"


def artifact_dto(artifact: Artifact, operation_state: str | None) -> ArtifactDTO:
    dto = ArtifactDTO.model_validate(artifact)
    dto.operation_state = operation_state
    return dto


def application_dto(
    application: Application,
    artifacts_with_states: list[tuple[Artifact, str | None]],
    *,
    job: JobDTO | None = None,
) -> ApplicationDTO:
    # Built explicitly (not model_validate) so we never lazy-load the ORM
    # relationship and packetState stays purely derived.
    artifact_dtos = [artifact_dto(a, state) for a, state in artifacts_with_states]
    packet_state = derive_packet_state([state for _a, state in artifacts_with_states])
    # Per-kind slot states (US-RES-02 / US-CL-01): the resume and cover artifacts
    # are independent — derived from the head artifact of each kind + its op state.
    by_kind = {a.kind: (a, state) for a, state in artifacts_with_states}
    resume_a, resume_state = by_kind.get("tailored_resume", (None, None))
    cover_a, cover_state = by_kind.get("cover_letter", (None, None))
    return ApplicationDTO(
        id=application.id,
        job_id=application.job_id,
        job=job,
        column=application.column,
        priority=application.priority,
        intent=application.intent,
        notes_markdown=application.notes_markdown,
        applied_via=application.applied_via,
        preview_screenshot_path=application.preview_screenshot_path,
        archived_at=application.archived_at,
        saved_at=application.saved_at,
        last_touched_at=application.last_touched_at,
        packet_state=packet_state,
        packet_resume_state=derive_artifact_state(resume_a, resume_state),
        packet_cover_letter_state=derive_artifact_state(cover_a, cover_state),
        artifacts=artifact_dtos,
    )


def schedule_dto(schedule: Schedule) -> ScheduleDTO:
    return ScheduleDTO.model_validate(schedule)


def profile_dto(profile: MasterProfile) -> ProfileDTO:
    return ProfileDTO.model_validate(profile)


def preferences_dto(prefs: UserPreferences) -> PreferencesDTO:
    return PreferencesDTO.model_validate(prefs)


def engine_setting_dto(row: EngineSettings) -> EngineSettingDTO:
    return EngineSettingDTO(
        id=row.id,
        engine=row.engine,
        base_url=row.base_url,
        default_model=row.default_model,
        enabled=row.enabled,
        has_key=row.key_ref is not None or row.key_encrypted is not None,
        key_hint=row.key_ref,
    )


def operation_dto(op: Operation) -> OperationDTO:
    return OperationDTO.model_validate(op)


def cost_totals_dto(totals: dict[str, Any]) -> CostTotalsDTO:
    """Build the all-time cost DTO from a repo cost aggregate (repos.CostTotals)."""
    return CostTotalsDTO(
        usd=float(totals.get("usd", 0.0)),
        tokens_in=int(totals.get("tokens_in", 0)),
        tokens_out=int(totals.get("tokens_out", 0)),
        operations=int(totals.get("operations", 0)),
        failed=int(totals.get("failed", 0)),
        by_kind={k: float(v) for k, v in (totals.get("by_kind") or {}).items()},
    )
