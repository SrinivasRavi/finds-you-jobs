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
    Contact,
    EngineSettings,
    Job,
    JobScore,
    LinkedInSession,
    MasterProfile,
    Operation,
    OutreachLog,
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
    # Referral progress for the tracker card's Referrals slot (FR-NW-01 canonical
    # enum). Derived, not stored: `none` (→ `notStarted` frontend) | `finding` |
    # `pending` | `sending` | `reachedOut` | `failed`. See derive_referrals_state.
    referrals_state: str = Field(default="none", serialization_alias="referralsState")
    referrals_count: int = Field(default=0, serialization_alias="referralsCount")
    # Latest Applier run for the card's Apply slot (`docs/internal/applier.md`
    # §8.2/§9.1): none | waiting_for_packet | running | ready_for_human |
    # blocked | timed_out | interrupted | failed | submitted.
    apply_run_status: str = Field(default="none", serialization_alias="applyRunStatus")
    apply_run_id: str | None = Field(default=None, serialization_alias="applyRunId")
    artifacts: list[ArtifactDTO] = Field(default_factory=list)


class ApplyRunDTO(BaseModel):
    """One durable Applier attempt (`docs/internal/applier.md` §9.1) for the
    companion panel. `blockers`/`fields` are redacted evidence (labels/kinds,
    never raw form values); `screenshots` counts the evidence PNGs served by
    `GET /api/apply-runs/{id}/screenshots/{index}`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    application_id: str
    operation_id: str | None
    retry_of_run_id: str | None
    status: str
    phase: str
    source_url: str
    final_url: str
    summary: str
    blockers: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    screenshot_count: int = 0
    usage: dict[str, Any]
    steps: int
    submit_evidence: str
    started_at: datetime
    deadline_at: datetime | None
    ended_at: datetime | None


class ApplyStartRequest(BaseModel):
    """POST /api/applications/{id}/apply — no pre-confirm modal (§8.1); the
    click IS the action. `retry_of_run_id` links a Retry / Reopen-and-refill
    to the immutable prior run (§8.3). The `dev` knobs pass through to the op
    and are honored only when the sidecar runs with FYJ_APPLY_DEV=1."""

    retry_of_run_id: str | None = None
    dev: dict[str, Any] | None = None


class ApplyAttestRequest(BaseModel):
    """POST /api/apply-runs/{id}/attest — the human says what happened after
    reviewing the P1 handoff (§8.4). `submitted=True` records a user-attested
    submission and moves the card to Applied; False leaves the card where it
    is with the honest run result."""

    submitted: bool


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


# ---------------------------------------------------------------------------
# Networking (database-design §5)
# ---------------------------------------------------------------------------


class NetworkingContactDTO(BaseModel):
    """One referral contact for a role, shown on the detail modal's Networking
    tab (US-TR-03 — visible only when LinkedIn is ON). Status + last outreach."""

    contact_id: str
    name: str
    role: str
    company: str
    linkedin_url: str
    connection_status: str
    ask_status: str | None = None
    audience_tag: str
    last_message: str | None = None
    last_message_at: datetime | None = None
    last_outcome: str | None = None


class ContactDTO(BaseModel):
    """One contact on the networking kanban (US-NW-01) / contact modal (US-NW-03)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    linkedin_url: str
    name: str
    current_role: str
    current_company: str
    headline: str
    connection_degree: int | None
    is_first_degree: bool
    audience_tag: str
    warmth: str
    connection_status: str
    added_at: datetime
    last_touched_at: datetime
    sent_at: datetime | None
    accepted_at: datetime | None
    archived_at: datetime | None
    # Derived last-message snippet for the kanban card (US-NW-01). P1 has no
    # reply detection (that's P2), so this is always the last message *we* sent.
    last_message: str | None = None
    last_message_at: datetime | None = None


class ContactCreate(BaseModel):
    """Manual add-a-contact (US-NW-02) — the rank-don't-gate escape hatch. The
    user can always add a contact by URL/name regardless of LinkedIn state."""

    linkedin_url: str
    name: str = ""
    current_company: str = ""
    current_role: str = ""
    # One of the live kanban columns (sent|accepted|engagement|converted).
    connection_status: str = "sent"
    audience_tag: str = "other"


class ContactUpdate(BaseModel):
    """Move between columns (US-NW-07) / archive / mark unresponsive."""

    connection_status: str | None = None
    audience_tag: str | None = None
    archived: bool | None = None


class ReferralCandidateDTO(BaseModel):
    """One row in the find-referrals popup (US-NW-09 / US-REF-01/02/03/10)."""

    contact_id: str
    name: str
    role: str
    company: str
    linkedin_url: str
    degree: int | None
    audience_tag: str  # peer | hm | recruiter | leadership | other
    warmth: str        # warm | cold
    channel: str       # dm | connection_note
    already_reached: bool
    # Whether this contact is in the role's persisted selection (FR-NW-01) — so a
    # reopened `pending` popup restores who the user picked, not just the roster.
    already_selected: bool = False
    # A ready-to-edit per-audience template draft (deterministic, zero-LLM). The
    # "Regenerate" action calls the LLM `draft` op for a grounded rewrite.
    draft: str


class ReferralCandidatesDTO(BaseModel):
    """The find-referrals popup payload for one role (US-NW-09)."""

    job_id: str
    company: str
    candidates: list[ReferralCandidateDTO]
    already_reached_count: int


class ReachOutContact(BaseModel):
    contact_id: str
    message: str


class DiscoverReferralsRequest(BaseModel):
    """Kick off / resume referral discovery for a role (US-REF-01 / FR-NW-02).

    `limit` bumps for the "find 10 more" control. `company_urn` (+ name/vanity/
    industry) is set only when re-calling after a `needs_company_confirm` event —
    it's the company the user picked in the confirm popup; the op caches + uses
    it, skipping resolution."""

    limit: int = 10
    page: int = 1  # "find 10 more" fetches the next results page
    # A user-picked candidate (from the confirm popup)…
    company_urn: str | None = None
    company_name: str | None = None
    company_vanity: str | None = None
    company_industry: str | None = None
    # …or a pasted LinkedIn company URL to resolve authoritatively (vanity → URN).
    company_url: str | None = None


class ReachOutRequest(BaseModel):
    """Batch reach-out (US-NW-09). Each contact gets ITS audience/warmth template
    (fanned out per person, not one string). Per-action confirmation lives in the
    UI; `dry_run` plans the sends without touching LinkedIn."""

    job_id: str | None = None
    application_id: str | None = None
    dry_run: bool = False
    contacts: list[ReachOutContact]


class ReachOutResult(BaseModel):
    """The enqueued send-operation ids, plus the contacts skipped as duplicates.

    `skipped_contact_ids` lists contacts that already had a queued/running send
    for this role — the idempotency guard against double-clicking "Send now"
    enqueuing duplicate real LinkedIn invites (US-NW-09)."""

    enqueued: list[str]
    skipped_contact_ids: list[str] = Field(
        default_factory=list, serialization_alias="skippedContactIds"
    )


class QuotaDTO(BaseModel):
    """Rolling outreach quota for the popup counter (US-NW-09 / US-NW-10).

    App-side view derived from the OutreachLog send windows + the account-tier
    caps. The *live* remaining cap is queried from the Referral Outreach package
    only on the maintainer's live-dogfood path."""

    connected: bool
    tier: str
    daily_used: int
    daily_limit: int
    weekly_used: int
    weekly_limit: int
    # 1st-degree DMs: tracked + displayed, never capped — they do not decrement
    # the invite counters above (FR-NW-04 / NFR-LI-02).
    dm_daily_sent: int = 0
    dm_weekly_sent: int = 0


class LinkedInSessionDTO(BaseModel):
    """LinkedIn session + master-toggle state (US-NW-09 / US-SET-06 / FR-SET-03).

    `enabled` is the master networking toggle (prefs.voyager_risk_marker_on);
    `status` is the session validity. The popup send path unlocks only when
    enabled AND status == 'valid'. N4 adds the session-capture metadata the
    Settings → LinkedIn session UI renders (connected-as, expiry, backoff)."""

    enabled: bool
    status: str        # valid | expired | never_set | connecting | backing_off
    account_tier: str  # new | seasoned
    connected_as: str = ""
    li_at_expires_at: datetime | None = None
    last_validated_at: datetime | None = None
    paused_until: datetime | None = None
    paused_reason: str = ""


class LinkedInConnectRequest(BaseModel):
    """Start the headed LinkedIn login (US-SET-06 as-built). `login_url` +
    `timeout_s` are maintainer/test overrides (a LOCAL fixture — never
    linkedin.com); production sends an empty body and uses the real login page."""

    login_url: str | None = None
    timeout_s: float | None = None


class LinkedInTierRequest(BaseModel):
    """Set the account-tier the app passes to the outreach package (US-REF-08)."""

    account_tier: str  # new | seasoned


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
    referrals_count: int = 0,
    referrals_op_states: list[str] | None = None,
    discover_in_flight: bool = False,
    has_candidates: bool = False,
    latest_batch_outcomes: list[str] | None = None,
    latest_apply_run: Any | None = None,
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
    referrals_state = derive_referrals_state(
        send_op_states=referrals_op_states or [],
        discover_in_flight=discover_in_flight,
        has_candidates=has_candidates,
        latest_batch_outcomes=latest_batch_outcomes or [],
    )
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
        referrals_state=referrals_state,
        referrals_count=referrals_count,
        apply_run_status=(
            latest_apply_run.status if latest_apply_run is not None else "none"
        ),
        apply_run_id=latest_apply_run.id if latest_apply_run is not None else None,
        artifacts=artifact_dtos,
    )


def apply_run_dto(run: Any) -> ApplyRunDTO:
    return ApplyRunDTO(
        id=run.id,
        application_id=run.application_id,
        operation_id=run.operation_id,
        retry_of_run_id=run.retry_of_run_id,
        status=run.status,
        phase=run.phase,
        source_url=run.source_url,
        final_url=run.final_url,
        summary=run.summary,
        blockers=list(run.blockers),
        fields=list(run.fields),
        screenshot_count=len(run.screenshots),
        usage=dict(run.usage),
        steps=run.steps,
        submit_evidence=run.submit_evidence,
        started_at=run.started_at,
        deadline_at=run.deadline_at,
        ended_at=run.ended_at,
    )


def template_draft(name: str, company: str, audience_tag: str, warmth: str) -> str:
    """A deterministic per-audience/warmth referral draft (US-NW-09 §9 8-template
    model). Zero-LLM — shown instantly in the popup, editable, and replaceable by
    a grounded LLM rewrite via the `draft` op. Mirrors the prototype copy."""
    first = (name.split(" ")[0] if name else "there")
    tag = audience_tag if audience_tag in ("peer", "hm", "recruiter", "leadership") else "peer"
    cold = {
        "peer": f"Hi {first}, I'm exploring roles at {company} and your work caught my "
                f"eye — would love to connect. Open to a quick chat?",
        "hm": f"Hi {first}, I'm very interested in a role you're hiring for at {company} "
              f"and would love to connect and share why I'd be a strong fit.",
        "recruiter": f"Hi {first}, I'm applying for a role at {company} and would love to "
                     f"connect — keen to learn about the team and process.",
        "leadership": f"Hi {first}, I admire what you're building at {company} and I'm "
                      f"exploring roles on the team — would love to connect.",
    }
    warm = {
        "peer": f"Hi {first}, hope you're well! I'm applying for a role on your team at "
                f"{company} and would really value your perspective — would you be open to "
                f"referring me, or pointing me to the right person?",
        "hm": f"Hi {first}, hope you're well. I'm very interested in a role you're hiring "
              f"for at {company} and believe I'd be a strong fit — would you be open to "
              f"considering my application directly?",
        "recruiter": f"Hi {first}, hope all's well! I'm applying for a role at {company} — "
                     f"would you be open to taking a look at my application or flagging it "
                     f"to the hiring team?",
        "leadership": f"Hi {first}, hope you're doing well. I admire what you're building at "
                      f"{company} and I'm applying for a role on the team — would you be open "
                      f"to a brief chat or to referring me?",
    }
    return (warm if warmth == "warm" else cold)[tag]


def contact_dto(contact: Contact, last_log: OutreachLog | None = None) -> ContactDTO:
    dto = ContactDTO.model_validate(contact)
    if last_log is not None:
        dto.last_message = last_log.body_sent
        dto.last_message_at = last_log.sent_at or last_log.created_at
    return dto


def referral_candidate_dto(
    contact: Contact, *, already_reached: bool, already_selected: bool = False
) -> ReferralCandidateDTO:
    channel = "dm" if contact.is_first_degree else "connection_note"
    return ReferralCandidateDTO(
        contact_id=contact.id,
        name=contact.name,
        role=contact.current_role,
        company=contact.current_company,
        linkedin_url=contact.linkedin_url,
        degree=contact.connection_degree,
        audience_tag=contact.audience_tag,
        warmth=contact.warmth,
        channel=channel,
        already_reached=already_reached,
        already_selected=already_selected,
        draft=template_draft(
            contact.name, contact.current_company, contact.audience_tag, contact.warmth
        ),
    )


# Account-tier rolling caps (US-NW-10 illustrative OpenOutreach defaults). NOT a
# hard contract — the outreach package owns the authoritative caps (§17).
# Surfaced for the popup's counter only.
_TIER_CAPS = {"new": (15, 100), "seasoned": (30, 200)}


def quota_dto(
    *, connected: bool, tier: str, daily_used: int, weekly_used: int,
    dm_daily_sent: int = 0, dm_weekly_sent: int = 0,
) -> QuotaDTO:
    daily_limit, weekly_limit = _TIER_CAPS.get(tier, _TIER_CAPS["new"])
    return QuotaDTO(
        connected=connected, tier=tier,
        daily_used=daily_used, daily_limit=daily_limit,
        weekly_used=weekly_used, weekly_limit=weekly_limit,
        dm_daily_sent=dm_daily_sent, dm_weekly_sent=dm_weekly_sent,
    )


def linkedin_session_dto(
    session: LinkedInSession | None, *, enabled: bool
) -> LinkedInSessionDTO:
    if session is None:
        return LinkedInSessionDTO(enabled=enabled, status="never_set", account_tier="new")
    return LinkedInSessionDTO(
        enabled=enabled,
        status=session.status,
        account_tier=session.account_tier,
        connected_as=session.connected_as,
        li_at_expires_at=session.li_at_expires_at,
        last_validated_at=session.last_validated_at,
        paused_until=session.paused_until,
        paused_reason=session.paused_reason,
    )


def derive_referrals_state(
    *,
    send_op_states: list[str],
    discover_in_flight: bool,
    has_candidates: bool,
    latest_batch_outcomes: list[str],
) -> str:
    """The canonical `referralsState` for a role's Tracker card (FR-NW-01).

    Precedence (transient in-flight states win so a spinner never goes stale):

    - `sending`  — a send op of the current batch is queued/running.
    - `finding`  — a discover op for this role is queued/running (and nothing is
      sending).
    - Otherwise the resting state comes from the *latest reach-out batch*
      (`latest_batch_outcomes`, one entry per OutreachLog row sharing the newest
      `batch_id`):
        - all members `sent`     → `reachedOut`
        - ≥1 sent, not all       → `pending`    (partial / cap-stopped batch)
        - a batch exists, 0 sent → `failed`     (all-failed — an honest extension
          of the 5-state spec)
    - No batch yet but candidates were discovered for the role → `pending`.
    - Nothing discovered or sent → `none` (frontend `notStarted`).
    """
    if any(state in ("queued", "running") for state in send_op_states):
        return "sending"
    if discover_in_flight:
        return "finding"
    if latest_batch_outcomes:
        sent = sum(1 for outcome in latest_batch_outcomes if outcome == "sent")
        if sent == len(latest_batch_outcomes):
            return "reachedOut"
        if sent > 0:
            return "pending"
        return "failed"
    if has_candidates:
        return "pending"
    return "none"


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
