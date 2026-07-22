// Real sidecar-backed client (ROADMAP A4).
//
// Talks to the live FastAPI sidecar over the bearer-authenticated loopback API
// (client.ts handshake). The wire DTOs come from the generated OpenAPI types
// (schema.d.ts) — so Pydantic↔TS drift is a build error — and are mapped here
// into the frontend view types (src/api/types.ts) the surfaces already consume.
//
// This repo's sidecar implements a SUBSET of the prior API surface so far
// (jobs/board/trash/tombstone/preview/add-by-url, operations, cost totals,
// schedules, profile, settings, engines, SSE, and now the full applications/
// tracker CRUD + packet/artifact endpoints). Networking/apply/prep/prompts/
// spans/ingest/dev-tools methods stay trimmed until their sidecar surface
// lands — see schema.d.ts for exactly what's live.
//
// FR-sync (2026-07-07): the live NormalizedJob has no work-style / applicants /
// skill-chips, so those render empty (work-style filter is best-effort location
// text per FR-JB-04).

import type { components } from "./schema";
import { apiFetch, getSidecarInfo, type SidecarInfo } from "./client";
import { JobTombstonedError } from "./types";
import type {
  Application,
  ApplicationProfile,
  ActivityEntry,
  ApplyRun,
  ApplyUsage,
  AudienceTag,
  BoardPage,
  CompanyConfirmPick,
  ContactInput,
  CostTotals,
  DevResult,
  EngineSaveInput,
  EngineVerifyResult,
  Job,
  JobDraft,
  LinkedInSessionState,
  NetContact,
  NetworkingContact,
  PacketState,
  Priority,
  ProfileIngestResult,
  DiscoveryAnalytics,
  DiscoveryCredential,
  DiscoverySource,
  PromptSetting,
  ScheduleRow,
  WatchCompanyResult,
  WatchlistEntry,
  ReachOutInput,
  ReachOutResult,
  ReferralCandidate,
  ReferralCandidates,
  ReferralQuota,
  Span,
  Stage,
  TombstoneResult,
  LedgerEntry,
  Operation,
  OnboardingPrefsInput,
  OperationKind,
  Profile,
  Settings,
  Warmth,
} from "./types";

type JobDTO = components["schemas"]["JobDTO"];
type BoardPageDTO = components["schemas"]["BoardPageDTO"];
type JobPreviewDTO = components["schemas"]["JobPreviewDTO"];
type TombstoneResultDTO = components["schemas"]["TombstoneResultDTO"];
type SettingsDTO = components["schemas"]["SettingsDTO"];
type ProfileDTO = components["schemas"]["ProfileDTO"];
type OperationDTO = components["schemas"]["OperationDTO"];
type ApplicationDTO = components["schemas"]["ApplicationDTO"];
type CostTotalsDTO = components["schemas"]["CostTotalsDTO"];
type SpanDTO = components["schemas"]["SpanDTO"];
type ActivityEntryDTO = components["schemas"]["ActivityEntryDTO"];
// Networking DTOs (restored 2026-07-16 — the referral-outreach backend now exists).
type NetworkingContactDTO = components["schemas"]["NetworkingContactDTO"];
type ContactDTO = components["schemas"]["ContactDTO"];
type ReferralCandidateDTO = components["schemas"]["ReferralCandidateDTO"];
type ReferralCandidatesDTO = components["schemas"]["ReferralCandidatesDTO"];
type QuotaDTO = components["schemas"]["QuotaDTO"];
type LinkedInSessionDTO = components["schemas"]["LinkedInSessionDTO"];
type ApplyRunDTO = components["schemas"]["ApplyRunDTO"];

// ─── operation kinds ─────────────────────────────────────────────────────────

const LLM_KINDS: OperationKind[] = ["score", "tailor", "cover", "extract", "prep"];

// ─── column ⇄ stage (restored from the prior repo's real.ts) ────────────────

const COLUMN_TO_STAGE: Record<string, Stage> = {
  saved: "Saved",
  seeking_referral: "Seeking Referral",
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
};
const STAGE_TO_COLUMN: Record<Stage, string> = {
  Saved: "saved",
  "Seeking Referral": "seeking_referral",
  Applied: "applied",
  Interviewing: "interviewing",
  Offer: "offer",
  Rejected: "rejected",
};

// Configurable entity-lifecycle windows (FR-SYS-06 / FR-NW-15). Defaults mirror
// the sidecar's `LIFECYCLE_DEFAULTS` so the UI reads the same values a fresh DB
// applies; a stored `ui_state.lifecycle` overrides per field.
const LIFECYCLE_DEFAULTS = {
  engagement_ghosted_days: 14,
  sent_ghosted_days: 21,
  contact_purge_days: 60,
  trashed_jobs_purge_days: 7,
  archived_applications_purge_days: 30,
  contact_sync_cadence_hours: 12,
} as const;

function readLifecycle(raw: unknown): Settings["lifecycle"] {
  const stored = (raw ?? {}) as Record<string, unknown>;
  const out = { ...LIFECYCLE_DEFAULTS } as Settings["lifecycle"];
  for (const key of Object.keys(LIFECYCLE_DEFAULTS) as (keyof typeof LIFECYCLE_DEFAULTS)[]) {
    const v = stored[key];
    if (typeof v === "number" && Number.isFinite(v) && v > 0) out[key] = v;
  }
  return out;
}

// ─── mappers (DTO → view type) ───────────────────────────────────────────────

function toJob(d: JobDTO, saved: boolean): Job {
  return {
    id: d.id,
    title: d.title,
    canonical_url: d.canonical_url,
    company: d.company,
    location: d.location,
    description: d.description,
    posted_at: d.posted_at ?? "",
    salary: d.salary ?? "",
    source_adapter: d.source_adapter,
    trust_score: d.trust_score,
    trust_flags: d.trust_flags,
    // work_style is derived app-side in the DTO (US-JB-01 chip / FR-JB-04 filter);
    // tags + applicants have no P1 source, so they stay empty on live rows (tags
    // are P2; applicants is mock-only — see the Job type FLAGGED note).
    work_style: (d.workStyle as Job["work_style"] | undefined) ?? "",
    tags: [],
    applicants: null,
    score: d.score
      ? {
          score_0_100: d.score.score_0_100,
          reasons: d.score.reasons as string[],
          breakdown_md: d.score.breakdown_md,
          scorer_impl: d.score.scorer_impl ?? "scorer-llm",
        }
      : null,
    score_status: (d.scoreStatus as Job["score_status"] | undefined) ?? (d.score ? "scored" : "pending"),
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
function placeholderJob(jobId: string): JobDTO {
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
  };
}

// Restored from the prior repo's real.ts, trimmed: no apply_state/form_prep/
// active_apply_operation_id (no Applier, no save-time prep surface on this
// sidecar yet) — preview_screenshot and posting_closed stay in the
// Application type but have no live source here, so they're hardcoded
// null/false. referrals_state/referrals_count restored 2026-07-16 (the
// referral-outreach backend now stamps them on every ApplicationDTO).
function toApplication(d: ApplicationDTO, job: Job): Application {
  const artifacts = d.artifacts ?? [];
  const resume = artifacts.find((a) => a.kind === "tailored_resume");
  const cover = artifacts.find((a) => a.kind === "cover_letter");
  return {
    id: d.id,
    job,
    stage: COLUMN_TO_STAGE[d.column] ?? "Saved",
    priority: d.priority as Priority,
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
    // Latest Apply Run lifecycle (applier.md §8.2) — drives the card's Apply slot
    // + reopening the companion to the bound run's snapshot (§9.2).
    apply_run_status: (d.applyRunStatus as Application["apply_run_status"]) ?? "none",
    apply_run_id: d.applyRunId ?? null,
    archived: d.archived_at != null,
    created_at: d.saved_at,
    updated_at: d.last_touched_at,
  };
}

// The run's usage dict is a redacted ledger snapshot (applier.md §9.1) — read
// the applier field names, falling back to the shared Usage names so an early
// sidecar shape still surfaces a cost line. `cost_usd` stays null when unknown.
function toApplyUsage(u: Record<string, unknown>): ApplyUsage {
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

function toApplyRun(d: ApplyRunDTO): ApplyRun {
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

function toOperation(d: OperationDTO): Operation {
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
          // null (unknown cost, e.g. an unpriced model) must stay null, never
          // collapse to 0 — a real paid call must never read as verified-free.
          usd: typeof d.usage.usd === "number" ? d.usage.usd : null,
          latency_ms: (d.usage.latency_ms as number | null) ?? null,
          model: (d.usage.model as string | null) ?? null,
        }
      : null,
    error: d.error ?? null,
    created_at: d.created_at,
  };
}

function toLedgerEntry(d: OperationDTO): LedgerEntry {
  const usage = d.usage ?? {};
  return {
    id: d.id,
    kind: d.kind as OperationKind,
    state: d.state as LedgerEntry["state"],
    // null (unknown cost, e.g. an unpriced model) must stay null, never
    // collapse to 0 — a real paid call must never read as verified-free.
    usd: typeof usage.usd === "number" ? usage.usd : null,
    tokens_in: Number(usage.tokens_in ?? 0),
    tokens_out: Number(usage.tokens_out ?? 0),
    model: (usage.model as string | null) ?? d.model ?? null,
    latency_ms: (usage.latency_ms as number | null) ?? null,
    error: d.error ?? null,
    created_at: d.created_at,
    started_at: d.started_at ?? null,
    subject: d.kind,
    // `context` is added by the backend but not yet in the codegen'd schema type.
    context: (d as { context?: string | null }).context ?? null,
    // Old→new retry link, stamped into the failed row's result_ref JSON.
    retried_as: ((d.result_ref as { retried_as?: string } | null)?.retried_as ?? null) as
      | string
      | null,
  };
}

// ─── the client ──────────────────────────────────────────────────────────────

/** An HTTP error carrying the sidecar status code, so callers can branch on it
 * (e.g. 409 → tombstoned URL → `JobTombstonedError`). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class RealApi {
  private infoP: Promise<SidecarInfo> | null = null;

  private info(): Promise<SidecarInfo> {
    // Never cache a rejected handshake: `??=` would pin the rejection and every
    // later call would fail even after the sidecar comes up (the Windows
    // white-window bug, 2026-07-19). Clear on failure so the next call retries.
    this.infoP ??= getSidecarInfo().catch((e: unknown) => {
      this.infoP = null;
      throw e;
    });
    return this.infoP;
  }

  private async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const info = await this.info();
    let res: Response;
    try {
      res = await apiFetch(info, path, init);
    } catch (e) {
      // Network-level failure (WebKit reports it as the bare "Load failed"):
      // the shell may have kill-restarted the sidecar on a NEW port/token
      // while we kept the old handshake — every request then dies against a
      // dead port for the rest of the session (observed 2026-07-22). Drop the
      // cached handshake and retry ONCE, but only when the re-resolved
      // handshake actually changed (restart evidence): the old port is dead,
      // so the retry cannot double-apply the original request.
      this.infoP = null;
      const fresh = await this.info();
      if (fresh.port === info.port && fresh.token === info.token) throw e;
      res = await apiFetch(fresh, path, init);
    }
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new ApiError(res.status, `${init.method ?? "GET"} ${path} → ${res.status}: ${body}`);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  private json(method: string, path: string, body: unknown): Promise<unknown> {
    return this.req(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  // ── jobs ───────────────────────────────────────────────────────────────
  // Restored: `/api/applications` now exists — cross-reference it so
  // `saved` reflects a real tracked card instead of always reporting false.
  async listJobs(): Promise<Job[]> {
    const [jobs, apps] = await Promise.all([
      this.req<JobDTO[]>("/api/jobs"),
      this.req<ApplicationDTO[]>("/api/applications"),
    ]);
    const saved = new Set(apps.map((a) => a.job_id));
    return jobs.map((d) => toJob(d, saved.has(d.id)));
  }

  /** One page of the board feed + header meta (FR-JB-02/10): saved-excluded,
   *  Expired-inclusive (greyed), paginated server-side (no silent 200-row cap).
   *  `listQ`/`textQ` (FR-JB-13) are server-side search filters applied before
   *  pagination — shallow (title/company/location) vs deep (JD + score texts). */
  async getBoard(page = 0, listQ = "", textQ = ""): Promise<BoardPage> {
    const params = new URLSearchParams({ page: String(page) });
    if (listQ) params.set("list_q", listQ);
    if (textQ) params.set("text_q", textQ);
    const d = await this.req<BoardPageDTO>(`/api/board?${params.toString()}`);
    return {
      jobs: (d.jobs ?? []).map((j) => toJob(j, false)),
      total: d.total,
      page: d.page,
      page_size: d.pageSize,
      scan_status: d.scanStatus as BoardPage["scan_status"],
      last_scan_at: d.lastScanAt ?? null,
      scan_error: d.scanError ?? null,
    };
  }

  /** Trashed jobs (US-JB-11) — the Trash modal's own source, kept off the board. */
  async listTrash(): Promise<Job[]> {
    const jobs = await this.req<JobDTO[]>("/api/jobs?feed_state=removed");
    return jobs.map((d) => toJob(d, false));
  }

  async getJob(id: string): Promise<Job | undefined> {
    try {
      return toJob(await this.req<JobDTO>(`/api/jobs/${id}`), false);
    } catch {
      return undefined;
    }
  }

  // Add-by-URL step 1 (US-JB-07): fetch + extract editable fields (no persist).
  async previewJob(url: string): Promise<JobDraft> {
    const d = (await this.json("POST", "/api/jobs/preview", { url }).catch((e: unknown) => {
      // 409 = the URL was permanently deleted (tombstoned) — final, not editable.
      if (e instanceof ApiError && e.status === 409) throw new JobTombstonedError();
      throw e;
    })) as JobPreviewDTO;
    return {
      canonical_url: d.canonical_url,
      title: d.title,
      company: d.company,
      location: d.location,
      description: d.description,
      salary: d.salary ?? "",
      source_adapter: d.source_adapter,
    };
  }

  // Add-by-URL step 2: persist the (edited) draft. The sidecar dedups against
  // active URLs, restores a Trashed one, and refuses a tombstoned one (2026-07-09).
  async addJobByUrl(draft: JobDraft): Promise<Job> {
    const d = (await this.json("POST", "/api/jobs", {
      canonical_url: draft.canonical_url,
      title: draft.title,
      company: draft.company,
      location: draft.location,
      description: draft.description,
      salary: draft.salary,
      source_adapter: draft.source_adapter || "paste-url",
    }).catch((e: unknown) => {
      if (e instanceof ApiError && e.status === 409) throw new JobTombstonedError();
      throw e;
    })) as JobDTO;
    return toJob(d, false);
  }

  // Empty Trash (US-JB-11 / FR-SYS-04): tombstone + remove every Trashed job.
  async emptyTrash(): Promise<TombstoneResult> {
    return (await this.json("POST", "/api/jobs/trash/empty", {})) as TombstoneResultDTO;
  }

  // Delete forever (US-JB-11): tombstone + remove one Trashed job.
  async tombstoneJob(id: string): Promise<TombstoneResult> {
    return (await this.json("POST", `/api/jobs/${id}/tombstone`, {})) as TombstoneResultDTO;
  }

  // Restored (`/api/applications` now exists): Save = POST an application row;
  // un-save = find it by job_id and DELETE it — the prior repo's model. Per-job
  // toggles (US-TL-03/US-JB-03); omitted → server falls back to the
  // auto-packet-on-save setting. `generate_prep` is accepted (JobBoard's Save
  // dialog still offers it) but dropped — no save-time prep surface on this
  // sidecar (ApplicationCreate carries no such field).
  async setJobSaved(
    id: string,
    saved: boolean,
    opts?: {
      generate_resume?: boolean | null;
      generate_cover?: boolean | null;
      generate_prep?: boolean | null;
    },
  ): Promise<Job | undefined> {
    if (saved) {
      await this.json("POST", "/api/applications", {
        job_id: id,
        ...(opts?.generate_resume != null && { generate_resume: opts.generate_resume }),
        ...(opts?.generate_cover != null && { generate_cover: opts.generate_cover }),
      });
    } else {
      const apps = await this.req<ApplicationDTO[]>("/api/applications");
      const app = apps.find((a) => a.job_id === id);
      if (app) await this.req(`/api/applications/${app.id}`, { method: "DELETE" });
    }
    return this.getJob(id);
  }

  async setJobBoardState(id: string, board_state: Job["board_state"]): Promise<Job | undefined> {
    const feed_state = board_state === "trashed" ? "removed" : "active";
    const d = (await this.json("PATCH", `/api/jobs/${id}`, { feed_state })) as JobDTO;
    return toJob(d, false);
  }

  // ── applications ─────────────────────────────────────────────────────────
  // Restored from the prior repo's real.ts, trimmed to the live endpoints (no
  // /networking, /apply, /prep — those surfaces haven't landed here). The job
  // rides embedded on each ApplicationDTO (server-side join) — never joined
  // against a capped /api/jobs list (the "(job removed)" bug).
  async listApplications(): Promise<Application[]> {
    const apps = await this.req<ApplicationDTO[]>("/api/applications");
    return apps.map((d) => toApplication(d, toJob(d.job ?? placeholderJob(d.job_id), true)));
  }

  async getApplication(id: string): Promise<Application | undefined> {
    try {
      const d = await this.req<ApplicationDTO>(`/api/applications/${id}`);
      return toApplication(d, toJob(d.job ?? placeholderJob(d.job_id), true));
    } catch {
      return undefined;
    }
  }

  /** Real Activity log for one application (US-TR-03 / FR-TR-03) — composed
   *  server-side from the ledger + card events, never synthesized. */
  async getApplicationActivity(id: string): Promise<ActivityEntry[]> {
    const rows = await this.req<ActivityEntryDTO[]>(`/api/applications/${id}/activity`);
    return rows.map((r) => ({
      kind: r.kind as ActivityEntry["kind"],
      label: r.label,
      state: r.state ?? null,
      at: r.at ?? null,
    }));
  }

  /** The role's referral contacts + statuses — detail-modal Networking tab
   *  (US-TR-03), restored 2026-07-16. */
  async getApplicationNetworking(id: string): Promise<NetworkingContact[]> {
    const rows = await this.req<NetworkingContactDTO[]>(`/api/applications/${id}/networking`);
    return rows.map((r) => ({
      contact_id: r.contact_id,
      name: r.name,
      role: r.role,
      company: r.company,
      linkedin_url: r.linkedin_url,
      connection_status: r.connection_status,
      ask_status: r.ask_status ?? null,
      audience_tag: r.audience_tag,
      last_message: r.last_message ?? null,
      last_message_at: r.last_message_at ?? null,
      last_outcome: r.last_outcome ?? null,
    }));
  }

  async listArchived(): Promise<Application[]> {
    const apps = await this.req<ApplicationDTO[]>("/api/applications?include_archived=true");
    return apps
      .filter((d) => d.archived_at != null)
      .map((d) => toApplication(d, toJob(d.job ?? placeholderJob(d.job_id), true)));
  }

  private async patchApp(id: string, body: unknown): Promise<Application | undefined> {
    const d = (await this.json("PATCH", `/api/applications/${id}`, body)) as ApplicationDTO;
    const job = await this.getJob(d.job_id);
    return toApplication(d, job ?? toJob(placeholderJob(d.job_id), true));
  }

  async updateApplication(id: string, patch: Partial<Application>): Promise<Application | undefined> {
    const body: Record<string, unknown> = {};
    if (patch.stage !== undefined) body.column = STAGE_TO_COLUMN[patch.stage];
    if (patch.priority !== undefined) body.priority = patch.priority;
    if (patch.notes !== undefined) body.notes_markdown = patch.notes;
    if (patch.intent !== undefined) body.intent = patch.intent;
    return this.patchApp(id, body);
  }

  moveApplication(id: string, stage: Stage): Promise<Application | undefined> {
    return this.patchApp(id, { column: STAGE_TO_COLUMN[stage] });
  }

  setPriority(id: string, priority: Priority): Promise<Application | undefined> {
    return this.patchApp(id, { priority });
  }

  async archiveApplication(id: string): Promise<void> {
    await this.json("PATCH", `/api/applications/${id}`, { archived: true });
  }

  async unarchiveApplication(id: string): Promise<void> {
    await this.json("PATCH", `/api/applications/${id}`, { archived: false });
  }

  async returnToBoard(id: string): Promise<void> {
    await this.req(`/api/applications/${id}`, { method: "DELETE" });
  }

  /** Manual/regenerate packet build (US-TL-02) — per-artifact: the tailor and
   *  cover modules are independent. `guidance` (FR-TL-02) reaches the Tailorer
   *  and is persisted with the variant. `_fail` exists only to match the
   *  MockApi arity (union call sites, restored from the prior repo) — the
   *  real path never simulates failure. */
  async generatePacket(
    appId: string,
    _fail = false,
    kinds: { resume: boolean; cover: boolean } = { resume: true, cover: true },
    guidance = "",
  ): Promise<void> {
    await this.json("POST", `/api/applications/${appId}/packet`, { ...kinds, guidance });
  }

  /** Persist an edited variant + the Approve-and-Save flip (US-RES-02 / FR-RES-02).
   *  `kind` maps the popup kind to the artifact kind. */
  async patchArtifact(
    appId: string,
    kind: "tailored" | "cover",
    patch: { markdown?: string; approved?: boolean },
  ): Promise<Application | undefined> {
    const artifactKind = kind === "cover" ? "cover_letter" : "tailored_resume";
    const d = (await this.json(
      "PATCH", `/api/applications/${appId}/artifacts/${artifactKind}`, patch,
    )) as ApplicationDTO;
    const job = await this.getJob(d.job_id);
    return toApplication(d, job ?? toJob(placeholderJob(d.job_id), true));
  }

  async packetState(appId: string): Promise<PacketState> {
    const d = await this.req<ApplicationDTO>(`/api/applications/${appId}`);
    return (d.packetState as PacketState) ?? "none";
  }

  // ── apply runs (the agentic Applier — applier.md §8/§9) ───────────────────
  // Starting Apply IS the action (§8.1): no pre-confirm modal, the run is
  // created and the op enqueued immediately. `retryOfRunId` links a Retry /
  // Reopen-and-refill to the immutable prior run (§8.3).
  async startApply(applicationId: string, retryOfRunId?: string): Promise<ApplyRun> {
    const body = retryOfRunId ? { retry_of_run_id: retryOfRunId } : {};
    const d = (await this.json(
      "POST", `/api/applications/${applicationId}/apply`, body,
    )) as ApplyRunDTO;
    return toApplyRun(d);
  }

  async listApplyRuns(applicationId: string): Promise<ApplyRun[]> {
    const rows = await this.req<ApplyRunDTO[]>(`/api/applications/${applicationId}/apply-runs`);
    return rows.map(toApplyRun);
  }

  /** The run snapshot — a reopened companion reads this instead of depending on
   *  having seen every prior SSE event (§9.2). */
  async getApplyRun(runId: string): Promise<ApplyRun> {
    return toApplyRun(await this.req<ApplyRunDTO>(`/api/apply-runs/${runId}`));
  }

  /** Cooperative cancel (§8.2) — the loop lands the run as `interrupted`. */
  async cancelApplyRun(runId: string): Promise<ApplyRun> {
    return toApplyRun((await this.json("POST", `/api/apply-runs/${runId}/cancel`, {})) as ApplyRunDTO);
  }

  /** The human's word after the P1 handoff (§8.4): `true` records a user-attested
   *  submission and advances the card to Applied; `false` leaves it in place. */
  async attestApplyRun(runId: string, submitted: boolean): Promise<ApplyRun> {
    return toApplyRun(
      (await this.json("POST", `/api/apply-runs/${runId}/attest`, { submitted })) as ApplyRunDTO,
    );
  }

  /** One evidence PNG as a Blob — the caller wraps it in an object URL for
   *  `<img>` (SSE can't set the Authorization header, so this authed fetch is
   *  the only way to load it). */
  async fetchApplyScreenshot(runId: string, index: number): Promise<Blob> {
    const info = await this.info();
    const res = await apiFetch(info, `/api/apply-runs/${runId}/screenshots/${index}`);
    if (!res.ok) {
      throw new ApiError(res.status, `GET screenshot ${runId}/${index} → ${res.status}`);
    }
    return res.blob();
  }

  // ── profile ────────────────────────────────────────────────────────────
  async getProfile(): Promise<Profile> {
    const d = await this.req<ProfileDTO | null>("/api/profile");
    return {
      master_md: d?.resume_markdown ?? "",
      version: d?.version ?? 1,
      application_profile:
        (d?.application_profile as Profile["application_profile"]) ?? null,
      entities: { skills: [], experiences: [], projects: [], education: [] },
    };
  }

  /** Manually (re-)extract the application profile (FR-APP-01). */
  async extractApplicationProfile(): Promise<string> {
    const d = (await this.json("POST", "/api/profile/extract", {})) as { id: string };
    return d.id;
  }

  /** Persist manual edits to the application profile — edits always win. */
  async patchApplicationProfile(fields: ApplicationProfile): Promise<Profile> {
    await this.json("PATCH", "/api/profile/application-profile", fields);
    return this.getProfile();
  }

  async updateProfile(master_md: string): Promise<Profile> {
    await this.json("POST", "/api/profile", { resume_markdown: master_md });
    return this.getProfile();
  }

  /** First-launch guard (FR-OB-01): a `MasterProfile` row exists ⟺ onboarded.
   *  `GET /api/profile` returns null before Finish. */
  async hasMasterProfile(): Promise<boolean> {
    const d = await this.req<ProfileDTO | null>("/api/profile");
    return d !== null;
  }

  /** Onboarding resume upload (FR-OB-04): multipart POST → extracted text for
   *  review. On failure surfaces the sidecar's **verbatim** detail (the
   *  paste-instead message) so the wizard can show it, never a silent empty draft. */
  async ingestResume(file: File): Promise<ProfileIngestResult> {
    const info = await this.info();
    const form = new FormData();
    form.append("file", file);
    // No explicit Content-Type — the browser sets the multipart boundary.
    const res = await apiFetch(info, "/api/profile/ingest", { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      let detail = body;
      try {
        detail = (JSON.parse(body) as { detail?: string }).detail ?? body;
      } catch {
        /* non-JSON body — surface as-is */
      }
      throw new Error(detail || `ingest failed (${res.status})`);
    }
    return (await res.json()) as ProfileIngestResult;
  }

  /** Render markdown → PDF into ~/Downloads via the sidecar's Chromium
   *  pipeline (US-RES-03 slice) — the webview can't print or download. */
  async exportPdf(markdown: string, filename: string): Promise<string> {
    const d = (await this.json("POST", "/api/export/pdf", { markdown, filename })) as {
      path: string;
    };
    return d.path;
  }

  /** Commit onboarding / Job-finder-preferences edits (FR-OB-05). Merges
   *  `scrape_cadence` into `ui_state` without clobbering the rest of the map. */
  async savePreferences(input: OnboardingPrefsInput): Promise<void> {
    const cur = await this.req<SettingsDTO>("/api/settings");
    const ui = {
      ...((cur.preferences.ui_state ?? {}) as Record<string, unknown>),
      scrape_cadence: input.scrape_cadence,
    };
    const body: Record<string, unknown> = {
      role_aliases: input.role_aliases,
      locations: input.locations,
      freshness_days: input.freshness_days,
      ui_state: ui,
    };
    // Personal excludes merge over the stored map (a future key like
    // `description` must survive a modal save that doesn't know about it).
    if (input.excluded_companies !== undefined || input.excluded_keywords !== undefined) {
      const stored = (cur.preferences.hard_excludes ?? {}) as Record<string, unknown>;
      body.hard_excludes = {
        ...stored,
        ...(input.excluded_companies !== undefined ? { companies: input.excluded_companies } : {}),
        ...(input.excluded_keywords !== undefined ? { keywords: input.excluded_keywords } : {}),
      };
    }
    // Only sent when the caller owns the decision (onboarding); the finder-
    // preferences modal edits scan prefs without touching the networking opt-in.
    if (input.networking_enabled !== undefined) {
      body.voyager_risk_marker_on = input.networking_enabled;
    }
    await this.json("POST", "/api/settings", body);
  }

  // ── settings ─────────────────────────────────────────────────────────────
  async getSettings(): Promise<Settings> {
    const d = await this.req<SettingsDTO>("/api/settings");
    const p = d.preferences;
    const thresholds = (p.thresholds ?? {}) as Record<string, unknown>;
    const routing = (p.engine_routing ?? {}) as Record<string, { engine?: string; model?: string }>;
    const ui = (p.ui_state ?? {}) as Record<string, unknown>;
    // Split Automation-on-Save defaults (FR-SET-02) — both default ON; fall back
    // to the legacy combined key, then to true.
    const legacy = "auto_packet_on_save" in thresholds ? Boolean(thresholds.auto_packet_on_save) : true;
    const autoResume =
      "auto_resume_on_save" in thresholds ? Boolean(thresholds.auto_resume_on_save) : legacy;
    const autoCover =
      "auto_cover_on_save" in thresholds ? Boolean(thresholds.auto_cover_on_save) : legacy;
    // Referral discovery on Save is OFF by default (it's the experimental,
    // account-risk path) and only meaningful when Referral Outreach is enabled.
    const autoReferrals = Boolean(thresholds.auto_referrals_on_save);
    // Applier submit mode (FR-APP-01): assisted (fill + hand off) by default.
    // Save-time form prep — default ON.
    // Scoring batch cap (audit P1-1) — 0 = uncapped, the planner's own default.
    const scoreNewBatch = Number(thresholds.score_new_batch ?? 0);
    return {
      auto_packet_on_save: autoResume && autoCover,
      auto_resume_on_save: autoResume,
      auto_cover_on_save: autoCover,
      auto_referrals_on_save: autoReferrals,
      scoring_mode: thresholds.scoring_mode === "keyword" ? "keyword" : "llm",
      llm_concurrency:
        typeof thresholds.llm_concurrency === "number" ? thresholds.llm_concurrency : 4,
      score_new_batch: scoreNewBatch,
      providers: d.engines.map((e) => ({
        id: e.engine,
        label: e.engine,
        configured: e.has_key,
        models: e.default_model ? [e.default_model] : [],
        base_url: e.base_url,
        default_model: e.default_model,
        key_hint: e.key_hint,
      })),
      routing: LLM_KINDS.map((kind) => ({
        kind,
        engine: routing[kind]?.engine ?? "claude-cli",
        model: routing[kind]?.model ?? "claude-opus-4-8",
      })),
      networking_enabled: p.voyager_risk_marker_on,
      networking_ack_at: (ui.networking_ack_at as string | undefined) ?? null,
      // LinkedIn one-shot per-query fetch budget (discovery-expansion #6);
      // persisted so the user's choice sticks. Default 50 (2 pages).
      linkedin_search_limit:
        typeof ui.linkedin_search_limit === "number" ? ui.linkedin_search_limit : 50,
      job_prefs: {
        role_aliases: (p.role_aliases ?? []).map(String),
        locations: (p.locations ?? []).map(String),
        freshness_days: p.freshness_days ?? 7,
        scrape_cadence: String(ui.scrape_cadence ?? "Every 24h"),
        excluded_companies: (
          ((p.hard_excludes ?? {}) as { companies?: unknown[] }).companies ?? []
        ).map(String),
        excluded_keywords: (
          ((p.hard_excludes ?? {}) as { keywords?: unknown[] }).keywords ?? []
        ).map(String),
      },
      observability: {
        content_logging: Boolean(ui.content_logging),
        otlp_enabled: Boolean(ui.otlp_enabled),
        otlp_endpoint: String(ui.otlp_endpoint ?? ""),
        otlp_headers: (ui.otlp_headers as Record<string, string> | undefined) ?? {},
        retention_days: Number(ui.retention_days ?? 30),
      },
      lifecycle: readLifecycle(ui.lifecycle),
    };
  }

  async updateSettings(patch: Partial<Settings>): Promise<Settings> {
    const cur = await this.req<SettingsDTO>("/api/settings");
    const p = cur.preferences;
    const body: Record<string, unknown> = {};
    // Split Automation-on-Save toggles (FR-SET-02); merge into thresholds so the
    // internal score_stats accumulator (FR-TR-09) is preserved.
    const thresholdPatch: Record<string, unknown> = {};
    if (patch.auto_resume_on_save !== undefined) thresholdPatch.auto_resume_on_save = patch.auto_resume_on_save;
    if (patch.auto_cover_on_save !== undefined) thresholdPatch.auto_cover_on_save = patch.auto_cover_on_save;
    if (patch.scoring_mode !== undefined) thresholdPatch.scoring_mode = patch.scoring_mode;
    if (patch.llm_concurrency !== undefined)
      thresholdPatch.llm_concurrency = patch.llm_concurrency;
    if (patch.auto_referrals_on_save !== undefined)
      thresholdPatch.auto_referrals_on_save = patch.auto_referrals_on_save;
    if (patch.auto_packet_on_save !== undefined) thresholdPatch.auto_packet_on_save = patch.auto_packet_on_save;
    if (patch.score_new_batch !== undefined)
      thresholdPatch.score_new_batch = patch.score_new_batch;
    if (Object.keys(thresholdPatch).length > 0) {
      body.thresholds = { ...(p.thresholds ?? {}), ...thresholdPatch };
    }
    if (patch.networking_enabled !== undefined) {
      body.voyager_risk_marker_on = patch.networking_enabled;
    }
    if (patch.routing !== undefined) {
      body.engine_routing = Object.fromEntries(
        patch.routing.map((r) => [r.kind, { engine: r.engine, model: r.model }]),
      );
    }
    const ui = { ...((p.ui_state ?? {}) as Record<string, unknown>) };
    let uiTouched = false;
    if (patch.networking_ack_at !== undefined) {
      ui.networking_ack_at = patch.networking_ack_at;
      uiTouched = true;
    }
    if (patch.linkedin_search_limit !== undefined) {
      ui.linkedin_search_limit = patch.linkedin_search_limit;
      uiTouched = true;
    }
    if (patch.observability !== undefined) {
      ui.content_logging = patch.observability.content_logging;
      ui.otlp_enabled = patch.observability.otlp_enabled;
      ui.otlp_endpoint = patch.observability.otlp_endpoint;
      ui.otlp_headers = patch.observability.otlp_headers;
      ui.retention_days = patch.observability.retention_days;
      uiTouched = true;
    }
    if (patch.lifecycle !== undefined) {
      // Persist the full lifecycle map under ui_state.lifecycle; the backend
      // reads it (FR-SYS-06) and threads the cadence to the contact_sync schedule.
      ui.lifecycle = { ...(ui.lifecycle as Record<string, unknown> | undefined), ...patch.lifecycle };
      uiTouched = true;
    }
    if (uiTouched) body.ui_state = ui;
    await this.json("POST", "/api/settings", body);
    return this.getSettings();
  }

  // ── /api/engines (BYOK provider surface — FR-SET-06 / US-SET-07) ─────────
  async verifyEngine(input: EngineSaveInput): Promise<EngineVerifyResult> {
    return (await this.json("POST", "/api/engines/verify", {
      provider: input.provider,
      key: input.key ?? null,
      base_url: input.base_url ?? null,
      model: input.default_model ?? null,
    })) as EngineVerifyResult;
  }
  async saveEngine(input: EngineSaveInput): Promise<void> {
    await this.json("POST", "/api/engines", {
      provider: input.provider,
      key: input.key ?? null,
      base_url: input.base_url ?? null,
      default_model: input.default_model ?? null,
      enabled: input.enabled ?? true,
    });
  }
  async deleteEngine(provider: string): Promise<void> {
    await this.req(`/api/engines/${provider}`, { method: "DELETE" });
  }

  // ── /api/settings/prompts (user-editable LLM prompts — FR-SET-11) ─────────
  // Server-driven list: whatever kinds the sidecar exposes (score/tailor/cover/
  // extract/draft/networker_draft — no prep) render in the Settings editor.
  async listPrompts(): Promise<PromptSetting[]> {
    return this.req<PromptSetting[]>("/api/settings/prompts");
  }
  async setPrompt(kind: string, markdown: string): Promise<PromptSetting> {
    return (await this.json(
      "PUT",
      `/api/settings/prompts/${kind}`,
      { markdown },
    )) as PromptSetting;
  }
  async resetPrompt(kind: string): Promise<PromptSetting> {
    return this.req<PromptSetting>(`/api/settings/prompts/${kind}`, { method: "DELETE" });
  }

  // ── /api/discovery/* (source toggles, BYO keys, watchlist, analytics) ────
  async listDiscoverySources(): Promise<DiscoverySource[]> {
    return this.req<DiscoverySource[]>("/api/discovery/sources");
  }
  /** Flip one source (string) or a whole Settings section (string[] — the
   *  section-title checkboxes; one atomic POST server-side). */
  async toggleDiscoverySource(
    idOrIds: string | string[],
    enabled: boolean,
  ): Promise<DiscoverySource[]> {
    const body = Array.isArray(idOrIds)
      ? { ids: idOrIds, enabled }
      : { id: idOrIds, enabled };
    return (await this.json("POST", "/api/discovery/sources", body)) as DiscoverySource[];
  }
  async listDiscoveryCredentials(): Promise<DiscoveryCredential[]> {
    return this.req<DiscoveryCredential[]>("/api/discovery/credentials");
  }
  async saveDiscoveryCredential(id: string, key: string): Promise<DiscoveryCredential[]> {
    return (await this.json("POST", "/api/discovery/credentials", {
      id,
      key,
    })) as DiscoveryCredential[];
  }
  async deleteDiscoveryCredential(id: string): Promise<DiscoveryCredential[]> {
    const res = await this.req<DiscoveryCredential[]>(`/api/discovery/credentials/${id}`, {
      method: "DELETE",
    });
    return res;
  }
  /** One-shot logged-in LinkedIn job search (discovery-expansion #6). Returns
   *  the enqueued op; results land in the normal feed. 403 (toggle off) / 409
   *  (not connected) surface as errors. */
  async linkedinSearch(limit?: number): Promise<{ id: string; kind: string; state: string }> {
    return (await this.json(
      "POST",
      "/api/linkedin/search",
      limit !== undefined ? { limit } : {},
    )) as { id: string; kind: string; state: string };
  }
  async watchCompany(input: {
    url?: string;
    job_id?: string;
    company?: string;
  }): Promise<WatchCompanyResult> {
    return (await this.json("POST", "/api/discovery/watchlist", input)) as WatchCompanyResult;
  }
  async getSchedules(): Promise<ScheduleRow[]> {
    return this.req<ScheduleRow[]>("/api/schedules");
  }
  async getWatchlist(): Promise<WatchlistEntry[]> {
    const d = await this.req<{ entries: WatchlistEntry[] }>("/api/discovery/watchlist");
    return d.entries;
  }
  async unwatchCompany(url: string): Promise<boolean> {
    const d = await this.req<{ removed: boolean }>(
      `/api/discovery/watchlist?url=${encodeURIComponent(url)}`,
      { method: "DELETE" },
    );
    return d.removed;
  }
  async getDiscoveryAnalytics(): Promise<DiscoveryAnalytics> {
    return this.req<DiscoveryAnalytics>("/api/discovery/analytics");
  }

  // ── operations / ledger ────────────────────────────────────────────────
  async listLedger(): Promise<LedgerEntry[]> {
    const ops = await this.req<OperationDTO[]>("/api/operations?limit=200");
    return ops.map(toLedgerEntry);
  }

  /** All-time cost totals (FR-SET-07 / US-LOG-01 #2) — live ledger + the pruned
   *  aggregate, so the Analytics tiles show lifetime spend, not the retained
   *  window. */
  async getCostTotals(): Promise<CostTotals> {
    const d = await this.req<CostTotalsDTO>("/api/cost/totals");
    return {
      usd: d.usd,
      tokens_in: d.tokens_in,
      tokens_out: d.tokens_out,
      operations: d.operations,
      failed: d.failed,
      by_kind: d.by_kind,
    };
  }

  /** The Logfire spans for an operation — the Logs drill-down (US-SYS-05). Reads
   *  the local logfire.sqlite store; [] when observability isn't configured. */
  async getOperationSpans(id: string): Promise<Span[]> {
    try {
      const spans = await this.req<SpanDTO[]>(`/api/operations/${id}/spans`);
      return spans.map((s) => ({
        span_id: s.span_id,
        name: s.name,
        operation_id: s.operation_id,
        op_kind: s.op_kind,
        duration_ms: s.duration_ms,
        status: s.status,
        attributes: s.attributes as Record<string, unknown>,
        events: (s.events as { name: string; attributes: Record<string, unknown> }[]) ?? [],
      }));
    } catch {
      return [];
    }
  }

  /** Re-run a failed op with its original inputs (US-LOG-01 Retry). */
  async retryOperation(id: string): Promise<Operation> {
    const d = (await this.json("POST", `/api/operations/${id}/retry`, {})) as {
      id: string;
      kind: string;
      state: string;
    };
    return {
      id: d.id,
      kind: d.kind as OperationKind,
      state: d.state as Operation["state"],
      progress: 0,
      step: d.state,
      usage: null,
      error: null,
      created_at: new Date().toISOString(),
    };
  }

  async getOperation(id: string): Promise<Operation | undefined> {
    try {
      return toOperation(await this.req<OperationDTO>(`/api/operations/${id}`));
    } catch {
      return undefined;
    }
  }

  async enqueueOperation(kind: OperationKind, _subject = "", _fail = false): Promise<Operation> {
    const d = (await this.json("POST", `/api/operations/${kind}`, {})) as {
      id: string;
      kind: string;
      state: string;
    };
    return {
      id: d.id,
      kind: d.kind as OperationKind,
      state: d.state as Operation["state"],
      progress: 0,
      step: d.state,
      usage: null,
      error: null,
      created_at: new Date().toISOString(),
    };
  }

  // ── networking (Track N3) — restored 2026-07-16 from the prior repo's
  // real.ts: the referral-outreach backend now exists. ────────────────────
  async listContacts(company?: string): Promise<NetContact[]> {
    const q = company ? `?company=${encodeURIComponent(company)}` : "";
    const rows = await this.req<ContactDTO[]>(`/api/contacts${q}`);
    return rows.map(toContact);
  }

  /** The "Deleted Contacts" recovery roster — archived rows only (US-NW-02). */
  async listArchivedContacts(): Promise<NetContact[]> {
    const rows = await this.req<ContactDTO[]>("/api/contacts?archived=true");
    return rows.map(toContact);
  }

  async addContact(input: ContactInput): Promise<NetContact> {
    const d = (await this.json("POST", "/api/contacts", {
      linkedin_url: input.linkedin_url,
      name: input.name ?? "",
      current_company: input.current_company ?? "",
      current_role: input.current_role ?? "",
      connection_status: input.connection_status ?? "sent",
      audience_tag: input.audience_tag ?? "other",
    })) as ContactDTO;
    return toContact(d);
  }

  async updateContact(id: string, patch: Partial<NetContact> & { archived?: boolean }): Promise<NetContact> {
    const body: Record<string, unknown> = {};
    if (patch.connection_status !== undefined) body.connection_status = patch.connection_status;
    if (patch.audience_tag !== undefined) body.audience_tag = patch.audience_tag;
    if (patch.archived !== undefined) body.archived = patch.archived;
    const d = (await this.json("PATCH", `/api/contacts/${id}`, body)) as ContactDTO;
    return toContact(d);
  }

  async listReferralCandidates(jobId: string): Promise<ReferralCandidates> {
    const d = await this.req<ReferralCandidatesDTO>(`/api/jobs/${jobId}/referrals/candidates`);
    return {
      job_id: d.job_id,
      company: d.company,
      already_reached_count: d.already_reached_count,
      candidates: (d.candidates ?? []).map(toCandidate),
      discover_state: (d.discover_state ?? "never") as ReferralCandidates["discover_state"],
      company_confirm: (d.company_confirm ?? []) as unknown as ReferralCandidates["company_confirm"],
      confirm_url_failed: Boolean(d.confirm_url_failed),
    };
  }

  async discoverReferrals(
    jobId: string,
    limit = 10,
    confirm?: CompanyConfirmPick,
    page = 1,
  ): Promise<string> {
    const body: Record<string, unknown> = { limit, page };
    if (confirm?.companyUrl) {
      body.company_url = confirm.companyUrl;
    } else if (confirm?.companyUrn) {
      body.company_urn = confirm.companyUrn;
      body.company_name = confirm.companyName ?? "";
      body.company_vanity = confirm.companyVanity ?? "";
      body.company_industry = confirm.companyIndustry ?? "";
    }
    const d = (await this.json(
      "POST",
      `/api/jobs/${jobId}/referrals/discover`,
      body,
    )) as { id: string };
    return d.id;
  }

  /** Grounded LLM rewrite of a contact's referral draft (US-REF-03 Regenerate).
   *  Enqueues a `draft` op; the drafted message lands in the operation's
   *  `result_ref` — the caller reads it via `getOperation` (not returned here). */
  async draftReferral(contactId: string, jobId?: string | null): Promise<string> {
    const d = (await this.json(
      "POST",
      `/api/contacts/${contactId}/draft`,
      { job_id: jobId ?? null },
    )) as { id: string };
    return d.id;
  }

  async reachOut(input: ReachOutInput): Promise<ReachOutResult> {
    const d = (await this.json("POST", "/api/referrals/reach-out", {
      job_id: input.job_id ?? null,
      application_id: input.application_id ?? null,
      dry_run: input.dry_run ?? false,
      contacts: input.contacts,
    })) as { enqueued: string[]; skippedContactIds?: string[] };
    return { enqueued: d.enqueued, skipped_contact_ids: d.skippedContactIds ?? [] };
  }

  async getReferralQuota(): Promise<ReferralQuota> {
    const d = await this.req<QuotaDTO>("/api/referrals/quota");
    return {
      connected: d.connected,
      tier: d.tier as ReferralQuota["tier"],
      daily_used: d.daily_used,
      daily_limit: d.daily_limit,
      weekly_used: d.weekly_used,
      weekly_limit: d.weekly_limit,
      dm_daily_sent: d.dm_daily_sent ?? 0,
      dm_weekly_sent: d.dm_weekly_sent ?? 0,
    };
  }

  async getLinkedInSession(): Promise<LinkedInSessionState> {
    return toLinkedInSession(await this.req<LinkedInSessionDTO>("/api/linkedin/session"));
  }

  // ── LinkedIn session capture (US-SET-06 / N4) — the connect/enable controls
  // live in Settings (not built on this repo yet); these methods are restored
  // so Settings can wire them up without another real.ts pass. ─────────────
  async connectLinkedIn(): Promise<string> {
    const d = (await this.json("POST", "/api/linkedin/connect", {})) as { id: string };
    return d.id;
  }

  async cancelLinkedInConnect(): Promise<void> {
    await this.json("POST", "/api/linkedin/cancel", {});
  }

  async disconnectLinkedIn(): Promise<LinkedInSessionState> {
    return toLinkedInSession((await this.json(
      "POST", "/api/linkedin/disconnect", {},
    )) as LinkedInSessionDTO);
  }

  async validateLinkedIn(): Promise<LinkedInSessionState> {
    return toLinkedInSession((await this.json(
      "POST", "/api/linkedin/validate", {},
    )) as LinkedInSessionDTO);
  }

  async resumeLinkedIn(): Promise<LinkedInSessionState> {
    return toLinkedInSession((await this.json(
      "POST", "/api/linkedin/resume", {},
    )) as LinkedInSessionDTO);
  }

  async setLinkedInTier(tier: "new" | "seasoned"): Promise<LinkedInSessionState> {
    return toLinkedInSession((await this.json(
      "POST", "/api/linkedin/tier", { account_tier: tier },
    )) as LinkedInSessionDTO);
  }

  // ── Dev tools (local fault injection — US-DEV-01) ──────────────────────────
  async devExpireCookie(): Promise<DevResult> {
    return (await this.json("POST", "/api/dev/linkedin/expire-cookie", {})) as DevResult;
  }
  async devFailRunning(): Promise<DevResult> {
    return (await this.json("POST", "/api/dev/operations/fail-running", {})) as DevResult;
  }
  async devSeedApplication(): Promise<DevResult> {
    return (await this.json("POST", "/api/dev/seed-application", {})) as DevResult;
  }
}

function toLinkedInSession(d: LinkedInSessionDTO): LinkedInSessionState {
  return {
    enabled: d.enabled,
    status: d.status as LinkedInSessionState["status"],
    account_tier: d.account_tier as LinkedInSessionState["account_tier"],
    connected_as: d.connected_as ?? "",
    li_at_expires_at: d.li_at_expires_at ?? null,
    last_validated_at: d.last_validated_at ?? null,
    paused_until: d.paused_until ?? null,
    paused_reason: d.paused_reason ?? "",
  };
}

function toContact(d: ContactDTO): NetContact {
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
    sent_at: d.sent_at ?? null,
    accepted_at: d.accepted_at ?? null,
  };
}

function toCandidate(d: ReferralCandidateDTO): ReferralCandidate {
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
