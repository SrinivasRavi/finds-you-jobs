// Real sidecar-backed client (ROADMAP A4).
//
// Talks to the live FastAPI sidecar over the bearer-authenticated loopback API
// (client.ts handshake). The wire DTOs come from the generated OpenAPI types
// (schema.d.ts) — so Pydantic↔TS drift is a build error — and are mapped here
// into the frontend view types (src/api/types.ts) the surfaces already consume.
//
// This repo's sidecar implements a SUBSET of the prior API surface so far
// (jobs/board/trash/tombstone/preview/add-by-url, operations, cost totals,
// schedules, profile, settings, engines, SSE). Tracker/networking/apply/prep/
// packet/prompts/spans/ingest/dev-tools methods are trimmed until their
// sidecar surface lands — see schema.d.ts for exactly what's live.
//
// FR-sync (2026-07-07): the live NormalizedJob has no work-style / applicants /
// skill-chips, so those render empty (work-style filter is best-effort location
// text per FR-JB-04).

import type { components } from "./schema";
import { apiFetch, getSidecarInfo, type SidecarInfo } from "./client";
import { JobTombstonedError } from "./types";
import type {
  ApplicationProfile,
  BoardPage,
  EngineSaveInput,
  EngineVerifyResult,
  Job,
  JobDraft,
  TombstoneResult,
  LedgerEntry,
  Operation,
  OnboardingPrefsInput,
  OperationKind,
  Profile,
  Settings,
} from "./types";

type JobDTO = components["schemas"]["JobDTO"];
type BoardPageDTO = components["schemas"]["BoardPageDTO"];
type JobPreviewDTO = components["schemas"]["JobPreviewDTO"];
type TombstoneResultDTO = components["schemas"]["TombstoneResultDTO"];
type SettingsDTO = components["schemas"]["SettingsDTO"];
type ProfileDTO = components["schemas"]["ProfileDTO"];
type OperationDTO = components["schemas"]["OperationDTO"];

// ─── operation kinds ─────────────────────────────────────────────────────────

const LLM_KINDS: OperationKind[] = ["score", "tailor", "cover", "extract", "prep"];

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
          usd: Number(d.usage.usd ?? 0),
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
    usd: Number(usage.usd ?? 0),
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
class ApiError extends Error {
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
    this.infoP ??= getSidecarInfo();
    return this.infoP;
  }

  private async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const info = await this.info();
    const res = await apiFetch(info, path, init);
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
  async listJobs(): Promise<Job[]> {
    // No `/api/applications` yet (tracker commit) — "saved" can't be derived
    // server-side, so every row reports unsaved until that surface lands.
    const jobs = await this.req<JobDTO[]>("/api/jobs");
    return jobs.map((d) => toJob(d, false));
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

  // `/api/applications` doesn't exist yet in this sidecar (tracker commit) —
  // there's nowhere to persist a Save. Signature kept so the board's Save
  // button + its existing error handling still compile against this client.
  async setJobSaved(
    _id: string,
    _saved: boolean,
    _opts?: {
      generate_resume?: boolean | null;
      generate_cover?: boolean | null;
      generate_prep?: boolean | null;
    },
  ): Promise<Job | undefined> {
    throw new Error("saving jobs lands with the tracker commit");
  }

  async setJobBoardState(id: string, board_state: Job["board_state"]): Promise<Job | undefined> {
    const feed_state = board_state === "trashed" ? "removed" : "active";
    const d = (await this.json("PATCH", `/api/jobs/${id}`, { feed_state })) as JobDTO;
    return toJob(d, false);
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
    const applyMode = thresholds.apply_mode === "auto" ? "auto" : "assisted";
    // Save-time form prep — default ON.
    const autoPrep =
      "auto_prep_on_save" in thresholds ? Boolean(thresholds.auto_prep_on_save) : true;
    // Scoring batch cap (audit P1-1) — 0 = uncapped, the planner's own default.
    const scoreNewBatch = Number(thresholds.score_new_batch ?? 0);
    return {
      auto_packet_on_save: autoResume && autoCover,
      auto_resume_on_save: autoResume,
      auto_cover_on_save: autoCover,
      auto_referrals_on_save: autoReferrals,
      apply_mode: applyMode,
      auto_prep_on_save: autoPrep,
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
      job_prefs: {
        role_aliases: (p.role_aliases ?? []).map(String),
        locations: (p.locations ?? []).map(String),
        freshness_days: p.freshness_days ?? 7,
        scrape_cadence: String(ui.scrape_cadence ?? "Every 24h"),
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
    if (patch.auto_referrals_on_save !== undefined)
      thresholdPatch.auto_referrals_on_save = patch.auto_referrals_on_save;
    if (patch.auto_packet_on_save !== undefined) thresholdPatch.auto_packet_on_save = patch.auto_packet_on_save;
    if (patch.apply_mode !== undefined) thresholdPatch.apply_mode = patch.apply_mode;
    if (patch.auto_prep_on_save !== undefined)
      thresholdPatch.auto_prep_on_save = patch.auto_prep_on_save;
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

  // ── operations / ledger ────────────────────────────────────────────────
  async listLedger(): Promise<LedgerEntry[]> {
    const ops = await this.req<OperationDTO[]>("/api/operations?limit=200");
    return ops.map(toLedgerEntry);
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
}
