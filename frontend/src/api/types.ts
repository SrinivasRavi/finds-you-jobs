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
 *  active_apply_operation_id/referrals_state/referrals_count — the Applier,
 *  save-time prep, and referral-outreach surfaces haven't landed on this
 *  sidecar. `intent` is new (§5.1 exclusive value on ApplicationUpdate). */
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
  archived: boolean;
  created_at: string;
  updated_at: string;
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
 *  contract). Narrower than the full prior-repo union — apply/networking/
 *  prompt kinds return with their own commits. */
export type OperationKind = "score" | "tailor" | "cover" | "extract" | "prep" | "scan";

export interface EngineRoute {
  kind: OperationKind;
  engine: string;
  model: string;
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
  /** Applier submit mode default (FR-APP-01): "assisted" (fill + hand off to
   *  the human — the default) or "auto" (legacy fill-and-submit). */
  apply_mode: "assisted" | "auto";
  /** Save-time form prep (FR-APP-01, 2026-07-11): visit the job's real form,
   *  inventory it, draft answers — default ON; per-job override at Save. */
  auto_prep_on_save: boolean;
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
