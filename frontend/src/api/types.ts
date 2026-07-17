// Typed API surface — mirrors the sidecar's architecture §4.2 shapes and the
// module result dataclasses (ROADMAP §4 / sidecar/modules/*/types.py).
//
// This repo's sidecar implements a SUBSET of the prior API surface so far:
// jobs/board/trash/tombstone/preview/add-by-url, operations, cost totals,
// schedules, profile, settings, engines, SSE. Tracker/networking/apply/prep/
// packet/prompts/spans/ingest/dev-tools types are trimmed until their
// sidecar surface lands — see schema.d.ts for exactly what's live.

// ─── Module result shapes (sidecar/modules/*/types.py) ──────────────────────

/** Usage/cost record every module call returns (the ledger row source). */
export interface Usage {
  internal_calls: number;
  tokens_in: number;
  tokens_out: number;
  usd: number;
  latency_ms: number | null;
  model: string | null;
}

/** ScoreResult — scorer/types.py. `reasons` is 2–4 bullets (US-JB-05). */
export interface ScoreResult {
  score_0_100: number;
  reasons: string[];
  breakdown_md: string;
}

// ─── /api/jobs ──────────────────────────────────────────────────────────────

/** Board lifecycle for a job row: active feed, Trash (US-JB-11), or Expired —
 *  greyed but still on the board (FR-SYS-03). */
export type BoardState = "active" | "trashed" | "expired";

/** Score lifecycle for a board row (FR-JB-07 / NFR-OFFLINE-02):
 *  `scored` (real 0–100) / `pending` (queued or not yet attempted) /
 *  `failed` (the score op errored, none in flight — the `Score failed` pill). */
export type ScoreStatus = "scored" | "pending" | "failed";

/**
 * Thrown by `previewJob` / `addJobByUrl` when the pasted URL was permanently
 * deleted (tombstoned). Trash is recoverable, a tombstone is final — a re-add
 * is impossible, so the modal shows the honest copy instead of an editable form
 * (2026-07-09 ruling; US-JB-07 / FR-JB-09).
 */
export class JobTombstonedError extends Error {
  constructor(message = "This job was permanently deleted and can't be re-added.") {
    super(message);
    this.name = "JobTombstonedError";
  }
}

/** Result of Empty Trash / Delete forever (US-JB-11 / FR-SYS-04). */
export interface TombstoneResult {
  tombstoned: number;
  canonical_urls: string[];
}

export type WorkStyle = "REMOTE" | "HYBRID" | "ONSITE" | "";

/**
 * Job — the NormalizedJob (scraper/types.py) plus the app-side columns the
 * board needs (score, saved, board_state). Snake_case matches the sidecar DTOs.
 *
 * FLAGGED (US-JB-01 row spec vs as-built contract): the story promises
 * `work_style`, skill `tags`, and `N applicants` per row, but the as-built
 * NormalizedJob contract (ROADMAP §4) carries none of them and no adapter
 * provides `applicants` — they render empty on the live path.
 */
export interface Job {
  id: string;
  title: string;
  canonical_url: string;
  company: string;
  location: string;
  description: string;
  posted_at: string; // ISO 8601 or ""
  salary: string;
  source_adapter: string;
  trust_score: number;
  trust_flags: string[];
  work_style: WorkStyle;
  /** Skill chips shown on the list row (US-JB-01). */
  tags: string[];
  /** Applicant count — no adapter provides this today (see FLAGGED note). */
  applicants: number | null;
  /** null while the scorer operation is still in flight. */
  score: ScoreResult | null;
  /** Score lifecycle — resolves a null score to `pending` vs `failed` (US-JB-06). */
  score_status: ScoreStatus;
  saved: boolean;
  board_state: BoardState;
}

/** One page of the paginated Job Board feed + header meta (FR-JB-02/10). */
export interface BoardPage {
  jobs: Job[];
  total: number;
  page: number;
  page_size: number;
  /** `running` (a scan is in flight) / `error` (last scan failed) / `empty`
   *  (no eligible rows) / `idle` (rows present). */
  scan_status: "running" | "error" | "empty" | "idle";
  last_scan_at: string | null;
  scan_error: string | null;
}

/**
 * JobDraft — the editable fields the Add-by-URL modal shows (US-JB-07). Step 1
 * (`previewJob`) fetches the pasted URL and returns this best-effort; the user
 * edits, then step 2 (`addJobByUrl`) persists it.
 */
export interface JobDraft {
  canonical_url: string;
  title: string;
  company: string;
  location: string;
  description: string;
  salary: string;
  source_adapter: string;
}

// ─── /api/applications (the tracker) — restored from the prior repo, trimmed to
// what this sidecar's ApplicationDTO actually carries (no apply/prep/referrals
// surface yet) ─────────────────────────────────────────────────────────────

export type Stage =
  | "Saved"
  | "Seeking Referral"
  | "Applied"
  | "Interviewing"
  | "Offer"
  | "Rejected";

export const STAGES: Stage[] = [
  "Saved",
  "Seeking Referral",
  "Applied",
  "Interviewing",
  "Offer",
  "Rejected",
];

/** Card-level triage priority — z-band against the score distribution (US-TR-10). */
export type Priority = "P0" | "P1" | "P2" | "P3";

/**
 * packetState — the card's mirror of the tailor+cover runner state
 * (architecture §4.2 long-op UX contract; AM5 = two operations).
 */
export type PacketState =
  | "none" // no packet generated yet — "Generate resume"
  | "generating" // tailor/cover operations running — "Generating…"
  | "ready" // packet available, awaiting approval — yellow pill (US-RES-02)
  | "approved" // reviewed + approved — green pill
  | "failed";

/** One real Activity-log event on the detail modal (US-TR-03 / FR-TR-03).
 *  Ledger + outreach kinds, plus user-driven card events (FR-TR-04). Kinds
 *  this sidecar doesn't emit yet (apply/prep/outreach) stay in the union —
 *  harmless, and saves a re-narrowing pass once those land. */
export interface ActivityEntry {
  kind:
    | "added"
    | "score"
    | "tailor"
    | "cover"
    | "apply"
    | "prep"
    | "outreach"
    | "column_change"
    | "notes"
    | "archive"
    | "unarchive";
  label: string;
  state: string | null;
  at: string | null;
}

/** A tracked application (one per saved/applied job). Trimmed from the prior
 *  repo's shape: no apply_state/form_prep/form_prep_summary/
 *  active_apply_operation_id — the Applier and save-time prep surfaces
 *  haven't landed on this sidecar. `intent` is new (§5.1 exclusive value on
 *  ApplicationUpdate). `referrals_state`/`referrals_count` restored
 *  (2026-07-16, referral-outreach frontend) — the networking surface now
 *  exists on this sidecar. */
export interface Application {
  id: string;
  job: Job;
  stage: Stage;
  priority: Priority;
  /** The §5.1 exclusive next-step marker — "none" | "referral" | "apply". */
  intent: "none" | "referral" | "apply";
  notes: string;
  /** Combined packet state (kept for the card-menu regen logic + Activity tab). */
  packet_state: PacketState;
  /** Per-artifact states (US-RES-02 / US-CL-01): the Resume and Cover-letter slots
   *  are driven independently — one generating/failing must not repaint the other. */
  packet_resume_state: PacketState;
  packet_cover_state: PacketState;
  /** operation ids backing the packet (tailor, cover) while generating. */
  packet_ops: string[];
  tailored_resume_md: string | null;
  tailored_notes: string[];
  /** Master-profile version each variant was generated from — drives the
   *  stale-variant warning (FR-RES-03). Null when the variant doesn't exist. */
  tailored_profile_version: number | null;
  cover_profile_version: number | null;
  cover_letter_md: string | null;
  cover_notes: string[];
  /** Applier preview screenshot (loadable URL / data URL) — US-TR-03 §17d.
   *  Always null on this sidecar (no Applier surface yet); the DTO field
   *  exists so this stays a straight DTO→type mapping. */
  preview_screenshot: string | null;
  /** Save-time liveness (2026-07-11): true when a prep run found the posting
   *  dead. Always false on this sidecar (no save-time prep surface yet). */
  posting_closed: boolean;
  /** Referral progress for the tracker Referrals slot (restored 2026-07-16) —
   *  the canonical FR-NW-01 enum (backend `none` renders as `notStarted`):
   *  `none` (grey) → `finding` (grey+spinner, discovery running) → `pending`
   *  (yellow — roster found, or a partial/cap-stopped batch) → `sending`
   *  (yellow+spinner, batch in flight) → `reachedOut` (green, all selected
   *  sent). `failed` (red) = latest batch all-failed. */
  referrals_state:
    | "none"
    | "finding"
    | "pending"
    | "sending"
    | "reachedOut"
    | "failed";
  referrals_count: number;
  /** Latest Apply Run lifecycle for the tracker Apply slot (applier.md §8.2/§9):
   *  `none` (no run — "Apply") → `waiting_for_packet`/`running` ("Applying…") →
   *  `ready_for_human` (P1 handoff — "Review & submit") → `submitted` (advanced
   *  to Applied). `blocked`/`timed_out`/`interrupted`/`failed` are the honest
   *  non-success terminals that offer "Retry". Mirrors ApplicationDTO.applyRunStatus. */
  apply_run_status: ApplyRunStatus;
  /** The bound Apply Run id — reopening the companion fetches this run's
   *  snapshot (§9.2). Null until the first Apply is started. */
  apply_run_id: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

// ─── Apply Runs (the agentic Applier — applier.md §8/§9) ────────────────────

/** One durable Apply Run's terminal/live status (applier.md §9.1). `none` is the
 *  application-level "no run yet" marker; a real run is one of the others. */
export type ApplyRunStatus =
  | "queued"
  | "none"
  | "waiting_for_packet"
  | "running"
  | "ready_for_human"
  | "blocked"
  | "timed_out"
  | "interrupted"
  | "failed"
  | "submitted";

/** A redacted blocker the agent hit — kind/label only, never a raw form value
 *  (applier.md §9.1). */
export interface ApplyBlocker {
  kind: string;
  detail: string;
  field_label: string;
}

/** A redacted per-field outcome — the truthful filled/struggled record shown in
 *  the §8.4 handoff summary. `ok` is the verified read-back result. */
export interface ApplyField {
  label: string;
  action: string;
  ok: boolean;
  note: string;
}

/** Exact model usage for one run — the companion's cost line (§8.2). */
export interface ApplyUsage {
  calls: number;
  tokens_in: number;
  tokens_out: number;
  /** Null when the provider's cost isn't known (e.g. a local model). */
  cost_usd: number | null;
}

/** One Applier attempt (applier.md §9.1) backing the companion panel. Maps
 *  ApplyRunDTO; `blockers`/`fields` are redacted evidence and `screenshot_count`
 *  is the number of evidence PNGs served by
 *  `GET /api/apply-runs/{id}/screenshots/{index}`. */
export interface ApplyRun {
  id: string;
  application_id: string;
  operation_id: string | null;
  /** Links a Retry / Reopen-and-refill to the immutable prior run (§8.3). */
  retry_of_run_id: string | null;
  status: ApplyRunStatus;
  phase: string;
  source_url: string;
  final_url: string;
  summary: string;
  blockers: ApplyBlocker[];
  fields: ApplyField[];
  screenshot_count: number;
  usage: ApplyUsage;
  steps: number;
  submit_evidence: string;
  started_at: string;
  deadline_at: string | null;
  ended_at: string | null;
}

/** One referral contact for a role on the detail modal's Networking tab
 *  (US-TR-03) — restored 2026-07-16 from the prior repo. Maps NetworkingContactDTO
 *  (`GET /api/applications/{id}/networking`). */
export interface NetworkingContact {
  contact_id: string;
  name: string;
  role: string;
  company: string;
  linkedin_url: string;
  connection_status: string;
  ask_status: string | null;
  audience_tag: string;
  last_message: string | null;
  last_message_at: string | null;
  last_outcome: string | null;
}

// ─── Networking (Track N3 — contacts kanban, find-referrals, quota) ─────────
// Restored 2026-07-16 from the prior repo's types.ts: the referral-outreach
// backend now exists (GET/POST /api/contacts, /api/jobs/{id}/referrals/*,
// /api/referrals/*, /api/linkedin/*) — see schema.d.ts for the live DTOs.

/** The P1 4+1 audience taxonomy (US-NW-09 / US-REF-02). */
export type AudienceTag = "peer" | "hm" | "recruiter" | "leadership" | "other";
/** Warmth split (US-REF-10): 1st-degree → warm DM; else cold connection-note. */
export type Warmth = "warm" | "cold";
/** Contact lifecycle. `candidate` = discovered, off the kanban; the rest are the
 *  kanban columns (US-NW-01). */
export type ConnectionStatus =
  | "candidate"
  | "sent"
  | "accepted"
  | "engagement"
  | "ghosted"
  | "converted";

/** One contact on the networking kanban / contact modal (US-NW-01/03). */
export interface NetContact {
  id: string;
  linkedin_url: string;
  name: string;
  current_role: string;
  current_company: string;
  headline: string;
  connection_degree: number | null;
  is_first_degree: boolean;
  audience_tag: AudienceTag;
  warmth: Warmth;
  connection_status: ConnectionStatus;
  last_message: string | null;
  last_message_at: string | null;
  sent_at: string | null;
  accepted_at: string | null;
}

/** One row in the find-referrals popup (US-NW-09 / US-REF-01/02/03/10). */
export interface ReferralCandidate {
  contact_id: string;
  name: string;
  role: string;
  company: string;
  linkedin_url: string;
  degree: number | null;
  audience_tag: AudienceTag;
  warmth: Warmth;
  channel: "dm" | "connection_note";
  already_reached: boolean;
  /** In the role's persisted selection (FR-NW-01) — restores the popup's picks
   *  when a `pending` popup is reopened. */
  already_selected: boolean;
  /** Deterministic per-audience template draft, editable before send. */
  draft: string;
}

export interface ReferralCandidates {
  job_id: string;
  company: string;
  candidates: ReferralCandidate[];
  already_reached_count: number;
}

/** One LinkedIn company entity a company name resolved to (FR-NW-02). Shown in
 *  the company-confirm step when discovery can't auto-pick (ambiguous name /
 *  no employer-domain match); the user taps the right one before discovery runs.
 *  Streamed in the `needs_company_confirm` networker SSE event. */
export interface CompanyCandidate {
  urn: string;
  company_id: string;
  name: string;
  vanity: string;
  industry: string;
  logo_url: string;
  website: string;
  domain_match: boolean;
}

/** The company the user confirmed in the picker — re-sent with discovery so the
 *  op scopes by that entity's URN (and caches the choice for the employer).
 *  Either a picked candidate (`companyUrn` + meta) OR a pasted LinkedIn company
 *  URL (`companyUrl`), which the backend resolves to the exact entity. */
export interface CompanyConfirmPick {
  companyUrn?: string;
  companyName?: string;
  companyVanity?: string;
  companyIndustry?: string;
  companyUrl?: string;
}

/** Rolling outreach quota for the popup counter (US-NW-09/10). */
export interface ReferralQuota {
  connected: boolean;
  tier: "new" | "seasoned";
  daily_used: number;
  daily_limit: number;
  weekly_used: number;
  weekly_limit: number;
  /** 1st-degree DMs: tracked + displayed, never capped (FR-NW-04) — they do
   *  not decrement the invite counters above. */
  dm_daily_sent: number;
  dm_weekly_sent: number;
}

/** LinkedIn session + master-toggle state (US-NW-09 / US-SET-06 / FR-SET-03).
 *  The send path unlocks only when `enabled` AND `status === "valid"`. The
 *  connect/enable controls live in Settings (not built on this repo yet —
 *  Networking only reads this for a read-only status pill). */
export interface LinkedInSessionState {
  enabled: boolean;
  status: "valid" | "expired" | "never_set" | "connecting" | "backing_off";
  account_tier: "new" | "seasoned";
  connected_as: string;
  li_at_expires_at: string | null;
  last_validated_at: string | null;
  paused_until: string | null;
  paused_reason: string;
}

/** Manual add-a-contact input (US-NW-02). */
export interface ContactInput {
  linkedin_url: string;
  name?: string;
  current_company?: string;
  current_role?: string;
  connection_status?: ConnectionStatus;
  audience_tag?: AudienceTag;
}

export interface ReachOutContactInput {
  contact_id: string;
  message: string;
}

/** Batch reach-out (US-NW-09). Each contact carries its own edited message. */
export interface ReachOutInput {
  job_id?: string | null;
  application_id?: string | null;
  dry_run?: boolean;
  contacts: ReachOutContactInput[];
}

/** Reach-out result: the enqueued send-op ids + the contacts skipped as
 *  duplicates (already had an in-flight send — the idempotency guard). */
export interface ReachOutResult {
  enqueued: string[];
  skipped_contact_ids: string[];
}

// ─── /api/profile ───────────────────────────────────────────────────────────

/** Structured form-fill facts (FR-APP-01, 2026-07-11) — extracted from the
 *  master resume at save (one small LLM call), user-editable in Settings; the
 *  Applier (future commit) reads this instead of regex-scraping the markdown. */
export interface ApplicationProfile {
  name?: string;
  first_name?: string;
  last_name?: string;
  email?: string;
  phone?: string;
  location?: string;
  country?: string;
  work_authorization?: string;
  links?: Record<string, string>;
  education?: {
    school?: string;
    degree?: string;
    discipline?: string;
    start_year?: string;
    end_year?: string;
  }[];
  /** "extracted" (LLM at save) or "edited" (manual Settings edits — win). */
  source?: "extracted" | "edited";
  profile_version?: number;
}

export interface Profile {
  master_md: string;
  /** Master-resume version (bumps on every edit) — the stale-variant baseline
   *  (FR-RES-03): a variant is stale when its profile version is behind this. */
  version: number;
  /** Null until the first extraction has run. */
  application_profile: ApplicationProfile | null;
  /** Extracted ProfileEntities used by the FR-TL-01 fabrication guard. */
  entities: {
    skills: string[];
    experiences: string[];
    projects: string[];
    education: string[];
  };
}

/** Extracted resume text from an onboarding upload, held in the wizard draft for
 *  review before it is persisted via `updateProfile` (FR-OB-04 / US-OB-02).
 *  Mirrors the sidecar `ProfileIngestResult` (api/ingest.py). */
export interface ProfileIngestResult {
  text: string;
  filename: string;
  chars: number;
}

/** Onboarding/job-finder preferences (FR-OB-05 / US-OB-03 / US-SET-01). Maps to
 *  the `UserPreferences` columns via `POST /api/settings`; `scrape_cadence`
 *  rides in `ui_state` and the backend threads it into the `scan` schedule
 *  server-side on every save (enables + retimes it — 2026-07-12 audit P0-1). */
export interface OnboardingPrefsInput {
  role_aliases: string[];
  locations: string[];
  freshness_days: number;
  scrape_cadence: string;
  /** Omitted → the stored value is left untouched (the job-finder-preferences
   *  modal edits scan prefs without flipping the networking opt-in). */
  networking_enabled?: boolean;
}

// ─── /api/settings ──────────────────────────────────────────────────────────

/** Operation kinds this repo's sidecar actually enqueues today (§4.2 long-op
 *  contract). Narrower than the full prior-repo union — apply/prompt kinds
 *  return with their own commits. Networking kinds restored 2026-07-16
 *  (referral-outreach frontend): discover/draft/send (US-NW-09), linkedin_login
 *  + archive_stale_contacts (N4 lifecycle), contact_sync (US-NW-12 status sync). */
export type OperationKind =
  | "score"
  | "tailor"
  | "cover"
  | "extract"
  | "prep"
  | "scan"
  // The agentic Applier's run op (applier.md §9) — lands in the ledger, so the
  // Analytics filter groups must cover it or its rows get silently hidden.
  | "apply"
  // Grounded copilot answer + daily feed maintenance (FR-SYS-03/04) — also
  // ledger'd, kept in the union for the same reason.
  | "cleanup_trash"
  | "discover"
  | "draft"
  | "send"
  | "linkedin_login"
  | "archive_stale_contacts"
  | "contact_sync";

export interface EngineRoute {
  kind: OperationKind;
  engine: string;
  model: string;
}

/** A user-editable LLM prompt (module skill markdown) — US-SET-12 / FR-SET-11.
 *  `default_md` is the shipped text; `override_md` is the saved edit or null
 *  (→ default). `routed` kinds also carry an engine/model selector; the one
 *  unrouted kind (`networker_draft`) is prompt-only. The list is server-driven
 *  (`GET /api/settings/prompts`), so whatever kinds the sidecar exposes render. */
export interface PromptSetting {
  kind: string;
  title: string;
  routed: boolean;
  default_md: string;
  override_md: string | null;
}

export interface ProviderConfig {
  id: string;
  label: string;
  configured: boolean;
  models: string[];
  /** Base URL (Local LLM) — null for cloud providers using their default. */
  base_url?: string | null;
  /** The chosen default model for this provider, if saved. */
  default_model?: string | null;
  /** Masked hint of the stored key (e.g. `sk-…abc4`) — never the key itself. */
  key_hint?: string | null;
}

/** Result of a provider Verify probe (FR-SET-06 / US-OB-04). `detail` carries the
 *  provider's verbatim message on failure (a 401 tells you the key is wrong). */
export interface EngineVerifyResult {
  ok: boolean;
  detail: string;
  provider: string;
  /** claude-cli branches onboarding on this: `not_found` (install) vs
   *  `not_logged_in` (open a terminal, log in) vs `error`. Other providers map
   *  ok→"ok" / not-ok→"error". Optional for back-compat with older payloads. */
  status?: "ok" | "not_found" | "not_logged_in" | "error";
}

/** Save/replace one provider's config. Omitting `key` keeps the sealed key. */
export interface EngineSaveInput {
  provider: string;
  key?: string;
  base_url?: string;
  default_model?: string;
  enabled?: boolean;
}

export interface Settings {
  /** Legacy combined flag = resume && cover. Kept for consumers that read a
   *  single value (JobBoard per-job slider seed); the Settings UI drives the
   *  split flags below. */
  auto_packet_on_save: boolean;
  /** Split Automation-on-Save defaults (FR-SET-02): resume + cover default ON. */
  auto_resume_on_save: boolean;
  auto_cover_on_save: boolean;
  /** Find referrals on Save — default OFF (experimental, account-risk); only
   *  effective when Referral Outreach (networking) is enabled. */
  auto_referrals_on_save: boolean;
  auto_score_on_scan: boolean;
  /** Applier submit mode default (FR-APP-01): "assisted" (fill + hand off to
   *  the human — the default) or "auto" (legacy fill-and-submit). */
  /** Save-time form prep (FR-APP-01, 2026-07-11): visit the job's real form,
   *  inventory it, draft answers — default ON; per-job override at Save. */
  /** Per-tick cap on how many jobs `score_new` scores in one scheduler pass
   *  (audit P1-1) — 0 = uncapped (planner default). Read by
   *  `plan_score_new` via `thresholds["score_new_batch"]`. */
  score_new_batch: number;
  providers: ProviderConfig[];
  routing: EngineRoute[];
  networking_enabled: boolean;
  /** ISO timestamp of the last time the user checked the Referral Outreach
   *  ToS-risk acknowledgment box and turned the toggle on (audit P2-5) — a
   *  durable record, so re-opening Settings shows *when* the risk was
   *  accepted rather than just the current toggle state. Null until the
   *  first enable. */
  networking_ack_at: string | null;
  /** The stored job-finder preferences (US-OB-03 / US-SET-01) — the scan's
   *  real inputs, so the finder-preferences modal edits actual values (the
   *  2026-07-12 audit found it seeded from hardcoded mock chips). */
  job_prefs: {
    role_aliases: string[];
    locations: string[];
    freshness_days: number;
    scrape_cadence: string;
  };
  observability: {
    content_logging: boolean;
    /** OTLP export opt-in (default OFF). Off ⇒ no exporter at all — the local
     *  span store is the only sink; nothing leaves the machine (NFR-OBS-02). */
    otlp_enabled: boolean;
    otlp_endpoint: string;
    /** Extra headers sent with every OTLP export (audit P2-3) — e.g. an
     *  API-key header for a hosted collector. UI edits it as
     *  "key1=val1,key2=val2"; parsed to this dict on the wire (matches
     *  `observability/config.py`'s `otlp_headers` parser). */
    otlp_headers: Record<string, string>;
    retention_days: number;
  };
  /** Configurable entity-lifecycle windows (FR-SYS-06 / FR-NW-15, 2026-07-15).
   *  Every auto-lifecycle timer — contact kanban ghosting, deleted-contact /
   *  trashed-job / archived-application purge, and the contact-status sync
   *  cadence — reads its window from here (persisted in `ui_state.lifecycle`).
   *  Days, except `contact_sync_cadence_hours`. */
  lifecycle: {
    engagement_ghosted_days: number;
    sent_ghosted_days: number;
    contact_purge_days: number;
    trashed_jobs_purge_days: number;
    archived_applications_purge_days: number;
    contact_sync_cadence_hours: number;
  };
}

// ─── /api/operations/{kind} + /api/operations/{id} ──────────────────────────

export type OperationState = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Operation {
  id: string;
  kind: OperationKind;
  state: OperationState;
  /** 0..1 progress hint for the UI (SSE-driven). */
  progress: number;
  /** free-text step label ("scoring…", "tailoring…"). */
  step: string;
  usage: Usage | null;
  /** verbatim error message on failure (never swallowed). */
  error: string | null;
  created_at: string;
}

// ─── Operations ledger (Logs/Analytics reads this — §10) ────────────────────

export interface LedgerEntry {
  id: string;
  kind: OperationKind;
  state: OperationState;
  usd: number;
  tokens_in: number;
  tokens_out: number;
  model: string | null;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null; // when the op went running (US-LOG-01 timestamp)
  subject: string; // human label ("Score · Backend @ Glean")
  context: string | null; // "<company> · <role> · #app" — what the op was for
  /** Set on a failed row that was re-run (US-LOG-01 Retry): the new op's id.
   *  The row renders as "Retried" instead of a permanently nagging FAILED. */
  retried_as?: string | null;
}

/** Result envelope for the Dev-tab fault-injection actions (US-DEV-01). */
export interface DevResult {
  ok: boolean;
  count?: number;
  removed_cookies?: number;
  note?: string;
  detail?: string;
  job_id?: string;
  application_id?: string;
}

/** All-time cost totals for the Analytics cost tiles (FR-SET-07 / US-LOG-01 #2).
 *  Live-ledger sum + the pruned-ops aggregate, so the figures survive the ~250-op
 *  ledger retention and stay honest as an install ages. Mirrors CostTotalsDTO. */
export interface CostTotals {
  usd: number;
  tokens_in: number;
  tokens_out: number;
  operations: number;
  failed: number;
  by_kind: Record<string, number>;
}

// ─── Logfire spans (Logs drill-down — US-SYS-05 / A6) ───────────────────────

/** One span from the local logfire.sqlite store, read for an operation's
 *  drill-down: timings + the engine-call breakdown (cost/tokens/latency live in
 *  `attributes`). Mirrors SpanDTO (sidecar/app/api/dto.py). */
export interface Span {
  span_id: string;
  name: string;
  operation_id: string | null;
  op_kind: string | null;
  duration_ms: number;
  status: string; // "OK" | "UNSET" | "ERROR"
  attributes: Record<string, unknown>;
  events: { name: string; attributes: Record<string, unknown> }[];
}
