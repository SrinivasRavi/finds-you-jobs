// The sidecar's DTOs mapped into the view types in types.ts, and the constants
// the frontend copies from the sidecar. Split out of real.ts (F-P3).

import type { components } from "../schema";
import type {
  Application,
  ApplicationDocument,
  ApplyRun,
  ApplyUsage,
  AudienceTag,
  Job,
  LedgerEntry,
  LedgerSubject,
  LinkCandidate,
  LinkedInSessionState,
  NetContact,
  Operation,
  OperationKind,
  PacketState,
  PlaceChip,
  PlaceSuggestion,
  Priority,
  ReferralCandidate,
  ScoreWeightPreset,
  ScoringMode,
  Settings,
  Stage,
  Warmth,
} from "../types";

// The sidecar's own constants, as literal types (sidecar/app/shared_constants.py):
// each copy below is checked against them, so drift is a compile error.
export type Shared = components["schemas"]["SharedConstants"];

/** The methods this build knows. A stored value outside this list reads as "llm",
 *  the same fallback the server applies. */
export const SCORING_MODE_ROWS: Record<Shared["scoring_mode"], true> = {
  llm: true,
  jev: true,
  local: true,
  keyword: true,
};
export const SCORING_MODES = Object.keys(SCORING_MODE_ROWS) as ScoringMode[];
export const SCORE_WEIGHT_PRESET_ROWS: Record<Shared["score_weight_preset"], true> = {
  balanced: true,
  location_first: true,
  seniority_first: true,
  skills_only: true,
};
export const SCORE_WEIGHT_PRESETS = Object.keys(SCORE_WEIGHT_PRESET_ROWS) as ScoreWeightPreset[];
// `weights.PRESET_LOCATION_PENALTY`. Picking a preset here writes the value
// rather than leaving the sidecar to infer it, so what the slider shows and
// what the score used are always the same number.
export const DEFAULT_LOCATION_PENALTY: Record<ScoreWeightPreset, number> = {
  balanced: 15,
  location_first: 35,
  seniority_first: 15,
  skills_only: 0,
} satisfies Shared["preset_location_penalty"];

export type JobDTO = components["schemas"]["JobDTO"];
export type BoardPageDTO = components["schemas"]["BoardPageDTO"];
export type JobPreviewDTO = components["schemas"]["JobPreviewDTO"];
export type AttachLinkResultDTO = components["schemas"]["AttachLinkResultDTO"];
export type LinkCandidateDTO = components["schemas"]["LinkCandidateDTO"];
export type SettingsDTO = components["schemas"]["SettingsDTO"];
export type ProfileDTO = components["schemas"]["ProfileDTO"];
export type OperationDTO = components["schemas"]["OperationDTO"];
export type ApplicationDTO = components["schemas"]["ApplicationDTO"];
export type SpanDTO = components["schemas"]["SpanDTO"];
export type ActivityEntryDTO = components["schemas"]["ActivityEntryDTO"];
// Networking DTOs (restored 2026-07-16 — the referral-outreach backend now exists).
export type NetworkingContactDTO = components["schemas"]["NetworkingContactDTO"];
export type ContactDTO = components["schemas"]["ContactDTO"];
export type ReferralCandidateDTO = components["schemas"]["ReferralCandidateDTO"];
export type ReferralCandidatesDTO = components["schemas"]["ReferralCandidatesDTO"];
export type QuotaDTO = components["schemas"]["QuotaDTO"];
export type LinkedInSessionDTO = components["schemas"]["LinkedInSessionDTO"];
export type ApplyRunDTO = components["schemas"]["ApplyRunDTO"];
export type ScanProgressDTO = components["schemas"]["ScanProgressDTO"];
export type ScoreRetryDTO = components["schemas"]["ScoreRetryDTO"];

// ─── operation kinds ─────────────────────────────────────────────────────────

// The routable LLM kinds, pinned to the sidecar's `LlmKind` schema (F-L3). The
// Record makes drift a compile error both ways: a retired kind is an excess key,
// a new one a missing key. The hand-kept list it replaced had drifted.
export type LlmKind = components["schemas"]["LlmKind"];
export const LLM_KIND_ROWS: Record<LlmKind, true> = {
  score: true,
  tailor: true,
  cover: true,
  extract: true,
  draft: true,
  apply: true,
};
export const LLM_KINDS = Object.keys(LLM_KIND_ROWS) as LlmKind[];

// ─── column ⇄ stage (restored from the prior repo's real.ts) ────────────────

export const COLUMN_TO_STAGE: Record<string, Stage> = {
  saved: "Saved",
  seeking_referral: "Seeking Referral",
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
};
export const STAGE_TO_COLUMN: Record<Stage, string> = {
  Saved: "saved",
  "Seeking Referral": "seeking_referral",
  Applied: "applied",
  Interviewing: "interviewing",
  Offer: "offer",
  Rejected: "rejected",
};

// Configurable entity-lifecycle windows (FR-SYS-06 / FR-NW-15). The sidecar's
// `LIFECYCLE_DEFAULTS`, checked against it, so the UI reads the same values a
// fresh DB applies; a stored `ui_state.lifecycle` overrides per field.
export const LIFECYCLE_DEFAULTS: Shared["lifecycle_defaults"] = {
  engagement_ghosted_days: 14,
  sent_ghosted_days: 21,
  expire_listing_days: 14,
  contact_purge_days: 60,
  trashed_jobs_purge_days: 7,
  archived_applications_purge_days: 30,
};

/** A stored location chip: a picked place keeps its shape, anything else is
 *  the plain string it was saved as (place-picker.md section 3). */
export function asPlaceChip(raw: unknown): PlaceChip {
  if (raw && typeof raw === "object" && "label" in raw) {
    const r = raw as Record<string, unknown>;
    return {
      label: String(r.label ?? ""),
      name: String(r.name ?? r.label ?? ""),
      level: String(r.level ?? "text") as PlaceSuggestion["level"],
      id: String(r.id ?? ""),
    };
  }
  return String(raw);
}

export function readLifecycle(raw: unknown): Settings["lifecycle"] {
  const stored = (raw ?? {}) as Record<string, unknown>;
  const out = { ...LIFECYCLE_DEFAULTS } as Settings["lifecycle"];
  for (const key of Object.keys(LIFECYCLE_DEFAULTS) as (keyof typeof LIFECYCLE_DEFAULTS)[]) {
    const v = stored[key];
    if (typeof v === "number" && Number.isFinite(v) && v > 0) out[key] = v;
  }
  return out;
}

// ─── mappers (DTO → view type) ───────────────────────────────────────────────

export function toJob(d: JobDTO, saved: boolean): Job {
  return {
    id: d.id,
    title: d.title,
    canonical_url: d.canonical_url ?? null,
    company: d.company,
    location: d.location,
    description: d.description,
    posted_at: d.posted_at ?? "",
    salary: d.salary ?? "",
    source_adapter: d.source_adapter,
    trust_score: d.trust_score,
    trust_flags: d.trust_flags,
    // work_style is derived app-side in the DTO (US-JB-01 chip / FR-JB-04 filter).
    work_style: (d.workStyle as Job["work_style"] | undefined) ?? "",
    score: d.score
      ? {
          score_0_100: d.score.score_0_100,
          reasons: d.score.reasons as string[],
          breakdown_md: d.score.breakdown_md,
          scorer_impl: d.score.scorer_impl ?? "scorer-llm",
          weighted: d.score.weighted ?? false,
          method: d.score.method ?? "llm",
        }
      : null,
    score_status: (d.scoreStatus as Job["score_status"] | undefined) ?? (d.score ? "scored" : "pending"),
    is_new: d.isNew ?? false,
    duplicates: d.duplicates ?? 0,
    duplicate_notice: d.duplicateNotice ?? false,
    saved,
    board_state:
      d.feed_state === "removed"
        ? "trashed"
        : d.feed_state === "expired"
          ? "expired"
          : "active",
  };
}

// Restored from the prior repo's real.ts — the "(job removed)" fallback for an
// application whose embedded job somehow came back null.
export function placeholderJob(jobId: string): JobDTO {
  return {
    id: jobId,
    canonical_url: "",
    title: "(job removed)",
    company: "",
    location: "",
    description: "",
    posted_at: null,
    salary: null,
    source_adapter: "unknown",
    trust_score: 0,
    trust_flags: [],
    feed_state: "expired",
    ingested_at: new Date().toISOString(),
    workStyle: "",
    score: null,
    scoreStatus: "pending",
    isNew: false,
    duplicates: 0,
    duplicateNotice: false,
  };
}

// preview_screenshot and posting_closed have no live source on this sidecar,
// so they're null and false; referrals_state and referrals_count come from every
// ApplicationDTO.
export function toApplication(d: ApplicationDTO, job: Job): Application {
  const artifacts = d.artifacts ?? [];
  const resume = artifacts.find((a) => a.kind === "tailored_resume");
  const cover = artifacts.find((a) => a.kind === "cover_letter");
  return {
    id: d.id,
    job,
    stage: COLUMN_TO_STAGE[d.column] ?? "Saved",
    priority: d.priority as Priority,
    origin: (d.origin as Application["origin"]) ?? "discovered",
    documents: (d.documents ?? []).map((doc) => ({
      document_id: doc.document_id,
      kind: doc.kind as ApplicationDocument["kind"],
      filename: doc.original_filename,
      mime_type: doc.mime_type,
      byte_size: doc.byte_size,
    })),
    intent: (d.intent as Application["intent"]) ?? "none",
    notes: d.notes_markdown,
    packet_state: (d.packetState as PacketState) ?? "none",
    packet_resume_state: (d.packetResumeState as PacketState) ?? "none",
    packet_cover_state: (d.packetCoverLetterState as PacketState) ?? "none",
    packet_ops: artifacts.map((a) => a.operation_id).filter((x): x is string => !!x),
    tailored_resume_md: resume?.markdown ? resume.markdown : null,
    tailored_notes: (resume?.notes ?? []) as string[],
    tailored_profile_version: resume?.profile_version ?? null,
    cover_profile_version: cover?.profile_version ?? null,
    cover_letter_md: cover?.markdown ? cover.markdown : null,
    cover_notes: (cover?.notes ?? []) as string[],
    preview_screenshot: null,
    posting_closed: false,
    referrals_state: (d.referralsState as Application["referrals_state"]) ?? "none",
    referrals_count: d.referralsCount ?? 0,
    // Latest Apply Run lifecycle (applier-as-built.md section 8.2) — drives the card's Apply slot
    // + reopening the companion to the bound run's snapshot (section 9.2).
    apply_run_status: (d.applyRunStatus as Application["apply_run_status"]) ?? "none",
    apply_run_id: d.applyRunId ?? null,
    archived: d.archived_at != null,
    created_at: d.saved_at,
    updated_at: d.last_touched_at,
  };
}

/** The one way to read an ApplicationDTO off the wire: the job rides embedded
 *  on the DTO (server-side join — never joined against a capped /api/jobs list,
 *  the "(job removed)" bug), with the placeholder as the last resort. Always
 *  `saved: true` — a card exists ⟺ the job is saved. */
export function appFromDto(d: ApplicationDTO): Application {
  return toApplication(d, toJob(d.job ?? placeholderJob(d.job_id), true));
}

// The run's usage dict is a redacted ledger snapshot (applier-as-built.md section 9.1) — read
// the applier field names, falling back to the shared Usage names so an early
// sidecar shape still surfaces a cost line. `cost_usd` stays null when unknown.
export function toApplyUsage(u: Record<string, unknown>): ApplyUsage {
  const num = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? v : null;
  const cost = num(u.cost_usd) ?? num(u.usd);
  return {
    calls: num(u.calls) ?? num(u.internal_calls) ?? 0,
    tokens_in: num(u.tokens_in) ?? 0,
    tokens_out: num(u.tokens_out) ?? 0,
    cost_usd: cost,
  };
}

export function toApplyRun(d: ApplyRunDTO): ApplyRun {
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  return {
    id: d.id,
    application_id: d.application_id,
    operation_id: d.operation_id ?? null,
    retry_of_run_id: d.retry_of_run_id ?? null,
    status: d.status as ApplyRun["status"],
    phase: d.phase,
    source_url: d.source_url,
    final_url: d.final_url,
    summary: d.summary,
    blockers: (d.blockers ?? []).map((b) => ({
      kind: str(b.kind),
      detail: str(b.detail),
      field_label: str(b.field_label),
    })),
    fields: (d.fields ?? []).map((f) => ({
      label: str(f.label),
      action: str(f.action),
      ok: Boolean(f.ok),
      note: str(f.note),
    })),
    screenshot_count: d.screenshot_count ?? 0,
    usage: toApplyUsage(d.usage ?? {}),
    steps: d.steps,
    submit_evidence: d.submit_evidence,
    started_at: d.started_at,
    deadline_at: d.deadline_at ?? null,
    ended_at: d.ended_at ?? null,
  };
}

/** The one statement of the cost invariant: a null/absent `usd` (unknown cost,
 *  e.g. an unpriced model) must stay null, never collapse to 0 — a real paid
 *  call must never read as verified-free. Exported for its unit test. */
export function usdOrNull(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}

/** What the enqueue/retry/cancel routes answer with (not in the codegen'd
 *  schema — they're declared untyped sidecar-side). */
export type OperationStubDTO = { id: string; kind: string; state: string };

/** The enqueue/retry/cancel responses carry only `{id, kind, state}` — the rest
 *  of an Operation is genuinely unknown until its ledger row lands, so the stub
 *  says so (no usage, no error, progress 0) instead of inventing values.
 *  Exported for its unit test. */
export function stubOperation(d: OperationStubDTO): Operation {
  return {
    id: d.id,
    kind: d.kind as OperationKind,
    state: d.state as Operation["state"],
    progress: 0,
    step: d.state,
    usage: null,
    error: null,
    created_at: new Date().toISOString(),
    // A stub is the 202 acknowledgement, so nothing has been produced yet.
    result_ref: null,
  };
}

export function toLinkCandidate(d: LinkCandidateDTO): LinkCandidate {
  return {
    title: d.title,
    company: d.company,
    location: d.location,
    similarity: d.similarity ?? null,
  };
}

export function toOperation(d: OperationDTO): Operation {
  const terminal = d.state === "succeeded" || d.state === "failed";
  return {
    id: d.id,
    kind: d.kind as OperationKind,
    state: d.state as Operation["state"],
    progress: terminal ? 1 : d.state === "running" ? 0.5 : 0,
    step: d.state,
    usage: d.usage
      ? {
          internal_calls: Number(d.usage.internal_calls ?? 0),
          tokens_in: Number(d.usage.tokens_in ?? 0),
          tokens_out: Number(d.usage.tokens_out ?? 0),
          usd: usdOrNull(d.usage.usd),
          latency_ms: (d.usage.latency_ms as number | null) ?? null,
          model: (d.usage.model as string | null) ?? null,
        }
      : null,
    error: d.error ?? null,
    created_at: d.created_at,
    result_ref: (d.result_ref as Record<string, unknown> | null) ?? null,
  };
}

export function toLedgerEntry(d: OperationDTO): LedgerEntry {
  const usage = d.usage ?? {};
  return {
    id: d.id,
    kind: d.kind as OperationKind,
    state: d.state as LedgerEntry["state"],
    usd: usdOrNull(usage.usd),
    tokens_in: Number(usage.tokens_in ?? 0),
    tokens_out: Number(usage.tokens_out ?? 0),
    model: (usage.model as string | null) ?? d.model ?? null,
    latency_ms: (usage.latency_ms as number | null) ?? null,
    error: d.error ?? null,
    created_at: d.created_at,
    started_at: d.started_at ?? null,
    subject: toSubject(d.subject),
    // Old→new retry link, stamped into the failed row's result_ref JSON.
    retried_as: ((d.result_ref as { retried_as?: string } | null)?.retried_as ?? null) as
      | string
      | null,
    // Cap/backoff refusal marker: the send op succeeded, but our own caps
    // refused the send in-band (result_ref carries error + verbatim reason).
    // Without this the ledger row read as a plain "Succeeded" while nothing
    // went out (live 2026-08-02 — the free-plan notes allowance).
    refusal: refusalOf(d.result_ref),
  };
}

/** The row's backend-computed subject — WHICH entity the op acted on
 *  (US-LOG-01 legibility). Null whenever the backend resolved nothing. */
export function toSubject(s: OperationDTO["subject"]): LedgerSubject | null {
  if (!s) return null;
  return {
    label: s.label ?? "",
    href: s.href ?? null,
    context: s.context ?? null,
    detail: s.detail ?? null,
    count: typeof s.count === "number" ? s.count : null,
    badge: s.badge ?? null,
    artifact: s.artifact
      ? {
          application_id: s.artifact.application_id,
          kind: s.artifact.kind === "cover" ? "cover" : "tailored",
        }
      : null,
  };
}

/** The in-band send refusal from a result_ref, or null. Exported for its unit
 *  test. */
export function refusalOf(ref: OperationDTO["result_ref"]): string | null {
  const r = ref as { error?: string; reason?: string } | null;
  if (r?.error !== "cap_or_backoff") return null;
  return r.reason || r.error;
}



export function toLinkedInSession(d: LinkedInSessionDTO): LinkedInSessionState {
  return {
    enabled: d.enabled,
    status: d.status as LinkedInSessionState["status"],
    connected_as: d.connected_as ?? "",
    li_at_expires_at: d.li_at_expires_at ?? null,
    last_validated_at: d.last_validated_at ?? null,
    paused_until: d.paused_until ?? null,
    paused_reason: d.paused_reason ?? "",
    search_cursor: d.search_cursor ?? null,
    rate_limits: d.rate_limits ?? null,
    contact_sync_last_at: d.contact_sync_last_at ?? null,
    contact_sync_last_outcome: d.contact_sync_last_outcome ?? null,
  };
}

export function toContact(d: ContactDTO): NetContact {
  return {
    id: d.id,
    linkedin_url: d.linkedin_url,
    name: d.name,
    current_role: d.current_role,
    current_company: d.current_company,
    headline: d.headline,
    connection_degree: d.connection_degree,
    is_first_degree: d.is_first_degree,
    audience_tag: d.audience_tag as AudienceTag,
    warmth: d.warmth as Warmth,
    connection_status: d.connection_status as NetContact["connection_status"],
    last_message: d.last_message ?? null,
    last_message_at: d.last_message_at ?? null,
    last_message_direction:
      (d.last_message_direction as NetContact["last_message_direction"]) ?? null,
    last_message_from: d.last_message_from ?? null,
    sent_at: d.sent_at ?? null,
    accepted_at: d.accepted_at ?? null,
    added_at: d.added_at,
  };
}

export function toCandidate(d: ReferralCandidateDTO): ReferralCandidate {
  return {
    contact_id: d.contact_id,
    name: d.name,
    role: d.role,
    company: d.company,
    linkedin_url: d.linkedin_url,
    degree: d.degree,
    audience_tag: d.audience_tag as AudienceTag,
    warmth: d.warmth as Warmth,
    channel: d.channel as ReferralCandidate["channel"],
    already_reached: d.already_reached,
    already_selected: d.already_selected ?? false,
    draft: d.draft,
  };
}
