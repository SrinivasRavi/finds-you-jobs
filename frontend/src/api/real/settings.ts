// The RealApi methods for the profile, settings, engines and prompts (F-P3).
// Each surface extends the one before it, so RealApi stays 1 class,
// and `real.ts` ends the chain.

import { inTauri } from "../../shell/host";
import type {
  ApplicationProfile,
  Country,
  EngineSaveInput,
  EngineVerifyResult,
  OnboardingPrefsInput,
  Profile,
  ProfileIngestResult,
  PromptSetting,
  ScoreWeightPreset,
  ScoringMethod,
  ScoringMode,
  Settings,
  SettingsPatch,
} from "../types";
import {
  asPlaceChip,
  DEFAULT_LOCATION_PENALTY,
  LLM_KINDS,
  type ProfileDTO,
  readLifecycle,
  SCORE_WEIGHT_PRESETS,
  SCORING_MODES,
  type SettingsDTO,
} from "./mappers";
import { ApplicationsApi } from "./applications";

// Typed preference columns, sent as-is (audit-2026-10-14.md section 8.10).
const AUTOPILOT_KEYS = [
  "autopilot_apply",
  "autopilot_invite",
  "autopilot_dm",
  "autopilot_min_score",
  "autopilot_applies_per_day",
  "autopilot_contacts_per_job",
] as const;

export class SettingsApi extends ApplicationsApi {
  // ── profile ────────────────────────────────────────────────────────────
  async getProfile(): Promise<Profile> {
    const d = await this.req<ProfileDTO | null>("/api/profile");
    return {
      master_md: d?.resume_markdown ?? "",
      version: d?.version ?? 1,
      application_profile:
        (d?.application_profile as Profile["application_profile"]) ?? null,
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

  /** Every country, by name, for the work-authorization editor. */
  async getCountries(): Promise<Country[]> {
    return this.req<Country[]>("/api/geo/countries");
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
    const form = new FormData();
    form.append("file", file);
    return this.postForm<ProfileIngestResult>("/api/profile/ingest", form, "ingest");
  }

  /** Render markdown → PDF via the sidecar's Chromium pipeline (US-RES-03
   *  slice); the webview can't print or download. In the app the shell then
   *  asks where to save it (S-C53), and `null` means the user cancelled; on
   *  the browser-only dev path the sidecar saves into ~/Downloads. */
  async exportPdf(markdown: string, filename: string): Promise<string | null> {
    const desktop = inTauri();
    const d = (await this.json("POST", "/api/export/pdf", {
      markdown,
      filename,
      destination: desktop ? "temp" : "downloads",
    })) as { path: string };
    if (!desktop) return d.path;
    const { invoke } = await import("@tauri-apps/api/core");
    const name = d.path.split(/[\\/]/).pop() ?? `${filename}.pdf`;
    return invoke<string | null>("save_export", { src: d.path, name });
  }

  /** Start the one-time Chromium download (A16). Progress arrives on the SSE
   *  stream as `browser_install` events; a second call while one runs is a no-op. */
  async installBrowser(): Promise<string> {
    const d = (await this.json("POST", "/api/system/install-browser", {})) as { status: string };
    return d.status;
  }

  /** The last Chromium download's state, for a client that missed its event (A21). */
  async browserInstallStatus(): Promise<{ state: string; message: string }> {
    return this.req<{ state: string; message: string }>("/api/system/install-browser");
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
      scoring_mode: SCORING_MODES.includes(thresholds.scoring_mode as ScoringMode)
        ? (thresholds.scoring_mode as ScoringMode)
        : "llm",
      scoring_methods: (d.scoring_methods ?? []).map((m) => ({
        id: m.id as ScoringMode,
        ready: Boolean(m.ready),
        needs: (m.needs ?? null) as ScoringMethod["needs"],
      })),
      llm_concurrency:
        typeof thresholds.llm_concurrency === "number" ? thresholds.llm_concurrency : 4,
      ai_queue_mode: thresholds.ai_queue_mode === "shared" ? "shared" : "per_kind",
      resume_location:
        thresholds.resume_location === "posting" || thresholds.resume_location === "none"
          ? thresholds.resume_location
          : "master",
      score_new_batch: scoreNewBatch,
      score_weight_preset: SCORE_WEIGHT_PRESETS.includes(
        thresholds.score_weight_preset as ScoreWeightPreset,
      )
        ? (thresholds.score_weight_preset as ScoreWeightPreset)
        : "balanced",
      score_weights:
        typeof thresholds.score_weights === "object" && thresholds.score_weights !== null
          ? (thresholds.score_weights as Record<string, number>)
          : {},
      score_location_penalty:
        typeof thresholds.score_location_penalty === "number"
          ? thresholds.score_location_penalty
          : DEFAULT_LOCATION_PENALTY[
              (thresholds.score_weight_preset as ScoreWeightPreset) ?? "balanced"
            ] ?? 15,
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
        model: routing[kind]?.model ?? "claude-opus-5-5",
      })),
      networking_enabled: p.voyager_risk_marker_on,
      autopilot_apply: p.autopilot_apply,
      autopilot_invite: p.autopilot_invite,
      autopilot_dm: p.autopilot_dm,
      autopilot_min_score: p.autopilot_min_score,
      autopilot_applies_per_day: p.autopilot_applies_per_day,
      autopilot_contacts_per_job: p.autopilot_contacts_per_job,
      networking_ack_at: (ui.networking_ack_at as string | undefined) ?? null,
      // LinkedIn Job Search — its own experimental opt-in (shares the session).
      // Typed preference columns since 2026-08-02 (the sidecar 403s on them).
      linkedin_search_enabled: Boolean(p.linkedin_search_enabled),
      linkedin_search_ack_at: p.linkedin_search_ack_at ?? null,
      // LinkedIn one-shot per-query fetch budget (discovery-expansion #6);
      // persisted so the user's choice sticks. Default 50 (2 pages).
      job_prefs: {
        role_aliases: (p.role_aliases ?? []).map(String),
        locations: (p.locations ?? []).map(asPlaceChip),
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

  async updateSettings(patch: SettingsPatch): Promise<Settings> {
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
    if (patch.ai_queue_mode !== undefined) thresholdPatch.ai_queue_mode = patch.ai_queue_mode;
    if (patch.resume_location !== undefined)
      thresholdPatch.resume_location = patch.resume_location;
    if (patch.auto_referrals_on_save !== undefined)
      thresholdPatch.auto_referrals_on_save = patch.auto_referrals_on_save;
    if (patch.auto_packet_on_save !== undefined) thresholdPatch.auto_packet_on_save = patch.auto_packet_on_save;
    if (patch.score_new_batch !== undefined)
      thresholdPatch.score_new_batch = patch.score_new_batch;
    if (patch.score_weight_preset !== undefined) {
      thresholdPatch.score_weight_preset = patch.score_weight_preset;
      // Picking a preset clears any custom sliders, because the sidecar lets
      // sliders outrank a preset and a preset the user just picked has to win.
      thresholdPatch.score_weights = {};
      thresholdPatch.score_location_penalty =
        DEFAULT_LOCATION_PENALTY[patch.score_weight_preset] ?? 15;
    }
    if (patch.score_weights !== undefined) thresholdPatch.score_weights = patch.score_weights;
    if (patch.score_location_penalty !== undefined)
      thresholdPatch.score_location_penalty = patch.score_location_penalty;
    if (Object.keys(thresholdPatch).length > 0) {
      body.thresholds = { ...(p.thresholds ?? {}), ...thresholdPatch };
    }
    if (patch.linkedin_search_enabled !== undefined) {
      body.linkedin_search_enabled = patch.linkedin_search_enabled;
    }
    if (patch.linkedin_search_ack_at !== undefined) {
      body.linkedin_search_ack_at = patch.linkedin_search_ack_at;
    }
    if (patch.networking_enabled !== undefined) {
      body.voyager_risk_marker_on = patch.networking_enabled;
    }
    for (const key of AUTOPILOT_KEYS) {
      if (patch[key] !== undefined) body[key] = patch[key];
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
      // Only the fields sent: the rest stay as the sidecar holds them.
      for (const [k, v] of Object.entries(patch.observability)) {
        if (v !== undefined) ui[k] = v;
      }
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
}
