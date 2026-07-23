// Onboarding wizard (US-OB-01..05 / FR-OB-01..07) — forward-only steps:
// Resume → Preferences → LLM provider (real Verify hard-gate) → All set.
// The stepper also displays "Download the app" as stage 1, always done — the
// user is running the app — so setup starts partway complete. The LinkedIn /
// Referral-Outreach opt-in is NOT an onboarding step (P1 decision 2026-07-12:
// meet the core app first); it lives in Settings with its ack + warning flow.
// The draft is held in component state AND mirrored to
// localStorage (FR-OB-02 resumable draft), so quitting mid-wizard and relaunching
// resumes where the user left off. Nothing hits the final MasterProfile /
// preferences tables until "Finish" (US-OB-01 atomic-ish commit): the verified
// engine is saved at the Verify step (POST /api/engines), and Finish commits the
// master profile + preferences, enqueues the cold-start scan (US-JB-09), and
// routes to /jobs — the first-launch guard (main.tsx) then keeps the wizard shut.

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Trans, useTranslation } from "react-i18next";

import { api } from "../api";
import { qk } from "../api/queries";
import type { EngineSaveInput, EngineVerifyResult } from "../api/types";
import { InfoDot } from "../shell/InfoDot";
import { LanguageSelect } from "../shell/LanguageSelect";
import { openLoginTerminal } from "../shell/openExternal";

// The wizard's interactive steps (i18n keys). The stepper *displays* "Download
// the app" ahead of these as stage 1, pre-completed, so the numbering the user
// sees is 1 Download ✓ → 2 Resume → 3 Preferences → 4 LLM provider → 5 All set.
const STEP_LABELS = [
  "onboarding.stepResume",
  "onboarding.stepPreferences",
  "onboarding.stepProvider",
  "onboarding.stepAllSet",
];
const DISPLAY_STEPS = ["onboarding.stepDownload", ...STEP_LABELS];

// label/hint are i18n keys (t() at render) under the locale's
// onboarding.providers.<id> node; the ids themselves are API values.
const PROVIDERS = [
  { id: "openrouter", label: "onboarding.providers.openrouter.label", hint: "onboarding.providers.openrouter.hint" },
  { id: "local", label: "onboarding.providers.local.label", hint: "onboarding.providers.local.hint" },
  { id: "anthropic", label: "onboarding.providers.anthropic.label", hint: "onboarding.providers.anthropic.hint" },
  { id: "openai", label: "onboarding.providers.openai.label", hint: "onboarding.providers.openai.hint" },
  { id: "claude-cli", label: "onboarding.providers.claude-cli.label", hint: "onboarding.providers.claude-cli.hint" },
  { id: "codex-cli", label: "onboarding.providers.codex-cli.label", hint: "onboarding.providers.codex-cli.hint" },
  { id: "antigravity-cli", label: "onboarding.providers.antigravity-cli.label", hint: "onboarding.providers.antigravity-cli.hint" },
];

// The subscription-CLI providers (no key, verify-only — mirrors the backend's
// engine_config.CLI_PROVIDERS). Per-provider guidance for the not_found /
// not_logged_in verify outcomes. The display strings are i18n keys (t() at
// render) under the locale's onboarding.providers.<id> node.
const CLI_PROVIDERS: Record<
  string,
  {
    name: string; // i18n key — short name for guidance copy ("Claude CLI", …)
    verifyHint: string; // i18n key
    loginCli: "claude" | "codex" | "agy";
    loginLabel: string; // i18n key
    installUrl: string;
    installName: string; // i18n key
  }
> = {
  "claude-cli": {
    name: "onboarding.providers.claude-cli.name",
    verifyHint: "onboarding.providers.claude-cli.verifyHint",
    loginCli: "claude",
    loginLabel: "onboarding.providers.claude-cli.loginLabel",
    installUrl: "https://docs.claude.com/en/docs/claude-code/overview",
    installName: "onboarding.providers.claude-cli.installName",
  },
  "codex-cli": {
    name: "onboarding.providers.codex-cli.name",
    verifyHint: "onboarding.providers.codex-cli.verifyHint",
    loginCli: "codex",
    loginLabel: "onboarding.providers.codex-cli.loginLabel",
    installUrl: "https://developers.openai.com/codex/cli",
    installName: "onboarding.providers.codex-cli.installName",
  },
  "antigravity-cli": {
    name: "onboarding.providers.antigravity-cli.name",
    verifyHint: "onboarding.providers.antigravity-cli.verifyHint",
    loginCli: "agy",
    loginLabel: "onboarding.providers.antigravity-cli.loginLabel",
    installUrl: "https://antigravity.google/",
    installName: "onboarding.providers.antigravity-cli.installName",
  },
};
const isCliProvider = (id: string) => id in CLI_PROVIDERS;

const FRESHNESS_DAYS: Record<string, number> = { "24h": 1, "7d": 7, "30d": 30 };

// The LLM-backed operation kinds that need a routed engine — mirrors the
// backend's LLM_KINDS (sidecar/app/registry/engine_config.py). The prior
// repository's `prep` is retired (applier.md §2); `draft`/`apply` route here.
const LLM_KINDS = ["score", "tailor", "cover", "extract", "draft", "apply"] as const;

const DRAFT_KEY = "fyj-onboarding-draft-v1";

interface Draft {
  step: number;
  resumeName: string;
  resumeText: string;
  aliases: string[];
  locations: string[];
  freshness: string;
  cadence: string;
  scoringMode: "llm" | "keyword";
  provider: string;
  verified: boolean;
}

function loadDraft(): Partial<Draft> {
  try {
    return JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "{}") as Partial<Draft>;
  } catch {
    return {};
  }
}

export function Onboarding() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [d0] = useState(loadDraft);

  // Clamp a resumed draft's step — an older draft may point past the current
  // last step (the LinkedIn step was removed 2026-07-12).
  const [step, setStep] = useState(Math.min(d0.step ?? 0, STEP_LABELS.length - 1));

  // Draft — resume (the reviewed/editable markdown is the source of truth).
  const [resumeName, setResumeName] = useState(d0.resumeName ?? "");
  const [resumeText, setResumeText] = useState(d0.resumeText ?? "");
  const [ingestBusy, setIngestBusy] = useState(false);
  const [ingestError, setIngestError] = useState("");

  // Draft — preferences.
  const [aliases, setAliases] = useState<string[]>(d0.aliases ?? []);
  const [aliasInput, setAliasInput] = useState("");
  const [locations, setLocations] = useState<string[]>(d0.locations ?? []);
  const [locInput, setLocInput] = useState("");
  const [freshness, setFreshness] = useState(d0.freshness ?? "7d");
  const [cadence, setCadence] = useState(d0.cadence ?? "Every 24h");
  // Scoring mode (2026-07-22): "llm" | "keyword" — replaces the retired
  // scoring off-switch. An old draft's autoScore=false maps to keyword (the
  // closest cost-saving equivalent now that unscored boards are gone).
  const [scoringMode, setScoringMode] = useState<"llm" | "keyword">(
    d0.scoringMode ?? ((d0 as { autoScore?: boolean }).autoScore === false ? "keyword" : "llm"),
  );

  // Draft — provider. The raw key/base-URL is NOT persisted (it is sealed
  // server-side on Verify success); `verified` is, so a resumed wizard whose
  // engine was already saved stays past the gate.
  const [provider, setProvider] = useState(d0.provider ?? "openrouter");
  const [providerInput, setProviderInput] = useState("");
  const [verifyState, setVerifyState] = useState<"idle" | "verifying" | "verified" | "failed">(
    d0.verified ? "verified" : "idle",
  );
  const [verifyError, setVerifyError] = useState("");
  // claude-cli only: `not_found` (install) vs `not_logged_in` (open a terminal,
  // log in) drive different guidance below. null → generic failure (verifyError).
  const [verifyStatus, setVerifyStatus] = useState<EngineVerifyResult["status"] | null>(null);
  // Success detail worth showing (claude-cli: "Logged in as <email> · <plan>").
  // Not persisted in the draft — a resumed wizard shows the generic verified line.
  const [verifyDetail, setVerifyDetail] = useState("");

  const [finishing, setFinishing] = useState(false);
  const [finishError, setFinishError] = useState("");

  // FR-OB-02: mirror the draft to localStorage on every change (raw key excluded).
  useEffect(() => {
    const draft: Draft = {
      step,
      resumeName,
      resumeText,
      aliases,
      locations,
      freshness,
      cadence,
      scoringMode,
      provider,
      verified: verifyState === "verified",
    };
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    } catch {
      /* storage full / unavailable — non-fatal */
    }
  }, [
    step,
    resumeName,
    resumeText,
    aliases,
    locations,
    freshness,
    cadence,
    scoringMode,
    provider,
    verifyState,
  ]);

  const needsInput = !isCliProvider(provider);
  const canVerify = !needsInput || providerInput.trim().length > 0;

  const canContinue =
    step === 0
      ? resumeText.trim().length > 0
      : step === 1
        ? aliases.length > 0 && locations.length > 0
        : step === 2
          ? verifyState === "verified"
          : true; // All set — Finish takes over

  // Progress bar (friction-reducer): "Download the app" is display-stage 1 and
  // always done, so the bar never reads 0%. % = completed stages / all stages;
  // the final "All set" screen reads 100% (onboarding is effectively complete).
  const progressPct =
    step >= STEP_LABELS.length - 1
      ? 100
      : Math.round(((step + 1) / DISPLAY_STEPS.length) * 100);

  function addChip(list: string[], set: (v: string[]) => void, value: string, clear: () => void) {
    const v = value.trim();
    if (v && !list.includes(v)) set([...list, v]);
    clear();
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    setIngestBusy(true);
    setIngestError("");
    try {
      const res = await api.ingestResume(file);
      setResumeText(res.text);
      setResumeName(res.filename);
    } catch (e) {
      // Honest failure — surface the verbatim "paste instead" message.
      setIngestError(e instanceof Error ? e.message : String(e));
    } finally {
      setIngestBusy(false);
    }
  }

  function providerVerifyInput(): EngineSaveInput {
    if (isCliProvider(provider)) return { provider };
    if (provider === "local") return { provider, base_url: providerInput.trim() };
    return { provider, key: providerInput.trim() };
  }

  async function verify() {
    setVerifyState("verifying");
    setVerifyError("");
    setVerifyStatus(null);
    setVerifyDetail("");
    const input = providerVerifyInput();
    try {
      const res = await api.verifyEngine(input);
      if (res.ok) {
        // Persist the verified config (BYOK key sealed server-side); this is NOT
        // the final commit — that is Finish. Saving here means a resumed wizard
        // (or a Finish after a quit) already has a working engine.
        // Subscription CLIs have nothing to persist: no key, no URL — POST
        // /api/engines rejects them as BYOK configs; verify alone is the gate.
        if (!isCliProvider(provider)) await api.saveEngine(input);
        // A CLI's detail names the account ("Logged in as …") — show it.
        if (isCliProvider(provider)) setVerifyDetail(res.detail);
        setVerifyState("verified");
      } else {
        setVerifyState("failed");
        setVerifyError(res.detail);
        setVerifyStatus(res.status ?? null);
      }
    } catch (e) {
      setVerifyState("failed");
      setVerifyError(e instanceof Error ? e.message : String(e));
    }
  }

  async function finish() {
    setFinishing(true);
    setFinishError("");
    try {
      // Commit the master profile (this flips the FR-OB-01 guard to "onboarded").
      await api.updateProfile(resumeText);
      // Commit preferences (role/location/freshness/cadence). The LinkedIn /
      // Referral-Outreach opt-in is Settings-only (P1 decision 2026-07-12) —
      // onboarding always commits it off.
      await api.savePreferences({
        role_aliases: aliases,
        locations,
        freshness_days: FRESHNESS_DAYS[freshness] ?? 7,
        scrape_cadence: cadence,
        networking_enabled: false,
      });
      // Route every LLM operation to the provider the user just verified —
      // otherwise the routing default (claude-cli) silently runs the whole
      // pipeline on a CLI the user may not even have (2026-07-12 fix: an
      // OpenRouter onboarding was billing the user's Claude session instead).
      // Empty model → the engine row's default_model (BYOK) or the CLI's own
      // configured default (codex-cli / antigravity-cli). claude-cli alone
      // skips this: it IS the routing default.
      if (provider !== "claude-cli") {
        await api.updateSettings({
          routing: LLM_KINDS.map((kind) => ({ kind, engine: provider, model: "" })),
        });
      }
      // Scoring mode: committed only when it differs from the backend
      // default (llm).
      if (scoringMode !== "llm") {
        await api.updateSettings({ scoring_mode: scoringMode });
      }
      // Cold-start scan (US-JB-09) — fire-and-forget, never blocks the redirect.
      await api.enqueueOperation("scan");
      try {
        localStorage.removeItem(DRAFT_KEY);
      } catch {
        /* ignore */
      }
      await qc.invalidateQueries({ queryKey: qk.onboarding });
      await qc.invalidateQueries({ queryKey: qk.profile });
      navigate("/jobs");
    } catch (e) {
      setFinishError(e instanceof Error ? e.message : String(e));
      setFinishing(false);
    }
  }

  return (
    // Small windows (a VMware Fusion guest capped at 1024×768 with display
    // scaling unavailable, a laptop with a tall taskbar + zoomed text, …)
    // can be shorter than the card's content — the LLM-provider step alone
    // lists eight options. #root is locked to `height:100vh; overflow:hidden`
    // app-wide, so scrolling has to happen HERE, not at the page level.
    // `overflow-y-auto` + `place-items-center` on the SAME element clips the
    // top of the overflow (a well-known CSS trap: centering shifts content
    // into negative scroll-position space that a scrollbar can't reach) — so
    // the outer div only scrolls, and a separate `min-h-full` (not `h-full`)
    // inner grid does the centering. `min-h-full` can grow past 100%, so
    // when the card is taller than the window there's no excess space left
    // to center within, and nothing gets clipped.
    <div className="h-screen overflow-y-auto bg-canvas">
      <div className="grid min-h-full place-items-center p-6">
      <div className="w-full max-w-xl rounded-2xl border border-border bg-surface p-6 shadow-lg">
        {/* Language — the very first control (maintainer 2026-07-24 #5), so a
            non-English speaker can switch before reading anything else. */}
        <div className="mb-4 flex items-center justify-end gap-2">
          <span className="text-[11.5px] text-ink-4">{t("appearance.language")}</span>
          <LanguageSelect
            testid="onboarding-language"
            className="rounded-md border border-border bg-surface px-1.5 py-0.5 text-[11.5px] text-ink-2"
          />
        </div>
        {/* Stepper */}
        <ol className="mb-6 flex items-center gap-2" data-testid="onboarding-stepper">
          {DISPLAY_STEPS.map((label, i) => {
            // Display index 0 is "Download the app" — always done (the user is
            // running the app). Wizard steps sit at display index i-1 === step.
            const done = i === 0 || i - 1 < step;
            const active = i - 1 === step;
            return (
              <li key={label} className="flex flex-1 items-center gap-2">
                <span
                  className={
                    "grid h-6 w-6 place-items-center rounded-full text-[11px] font-semibold " +
                    (done
                      ? "bg-good text-white"
                      : active
                        ? "bg-accent text-white"
                        : "bg-surface-3 text-ink-3")
                  }
                >
                  {i + 1}
                </span>
                <span
                  className={"hidden text-[11px] sm:block " + (active ? "text-ink" : "text-ink-4")}
                >
                  {t(label)}
                </span>
              </li>
            );
          })}
        </ol>

        {/* Progress bar — "Download the app" (stage 1, always done) is counted
            in, so this never reads 0% and setup starts partway complete. */}
        <div className="mb-6" data-testid="onboarding-progress">
          <div className="mb-1 text-right text-[11px] text-ink-4">
            <span data-testid="onboarding-progress-pct">
              {t("onboarding.pctComplete", { pct: progressPct })}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>

        <div className="min-h-[280px]" data-testid={`onboarding-step-${step}`}>
          {step === 0 ? (
            <div className="space-y-4">
              <h1 className="text-[18px] font-semibold text-ink">{t("onboarding.titleResume")}</h1>
              <p className="text-[13px] text-ink-3">{t("onboarding.resumeIntro")}</p>
              <label className="block rounded-lg border border-dashed border-border-2 bg-surface-2 p-4 text-center text-[13px] text-ink-3 hover:border-accent">
                <input
                  type="file"
                  accept=".md,.txt,.markdown,.pdf"
                  className="hidden"
                  data-testid="resume-upload"
                  onChange={(e) => void onFile(e.target.files?.[0])}
                />
                {ingestBusy ? (
                  <span>{t("onboarding.extracting")}</span>
                ) : resumeName ? (
                  <span className="text-ink">
                    {t("onboarding.resumeLoaded", { name: resumeName })}
                  </span>
                ) : (
                  <span>{t("onboarding.resumeChoose")}</span>
                )}
              </label>
              {ingestError ? (
                <div
                  className="rounded-md border border-bad/40 bg-bad-wash p-2 text-[12px] text-bad"
                  data-testid="ingest-error"
                >
                  {ingestError}
                </div>
              ) : null}
              <div>
                <div className="mb-1 text-[12px] text-ink-3">{t("onboarding.resumeTextLabel")}</div>
                <textarea
                  value={resumeText}
                  data-testid="resume-text"
                  onChange={(e) => setResumeText(e.target.value)}
                  placeholder={t("onboarding.resumePlaceholder")}
                  className="h-40 w-full resize-y rounded-md border border-border bg-surface px-3 py-2 font-mono text-[12px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
                />
              </div>
            </div>
          ) : step === 1 ? (
            <div className="space-y-4">
              <h1 className="text-[18px] font-semibold text-ink">{t("onboarding.titlePreferences")}</h1>
              <ChipField
                label={t("onboarding.aliasLabel")}
                testid="alias"
                items={aliases}
                input={aliasInput}
                setInput={setAliasInput}
                onAdd={() => addChip(aliases, setAliases, aliasInput, () => setAliasInput(""))}
                onRemove={(v) => setAliases(aliases.filter((a) => a !== v))}
                placeholder={t("onboarding.aliasPlaceholder")}
                hint={t("onboarding.aliasHint")}
              />
              <ChipField
                label={t("onboarding.locationLabel")}
                testid="location"
                items={locations}
                input={locInput}
                setInput={setLocInput}
                onAdd={() => addChip(locations, setLocations, locInput, () => setLocInput(""))}
                onRemove={(v) => setLocations(locations.filter((a) => a !== v))}
                placeholder={t("onboarding.locationPlaceholder")}
                hint={t("onboarding.locationHint")}
              />
              <div>
                <div className="mb-1 text-[12px] text-ink-3">{t("onboarding.freshnessLabel")}</div>
                <div className="flex gap-1.5">
                  {["24h", "7d", "30d"].map((f) => (
                    <Pill key={f} active={freshness === f} onClick={() => setFreshness(f)}>
                      {t(`onboarding.freshness.${f}`)}
                    </Pill>
                  ))}
                </div>
              </div>
              <div>
                <div className="mb-1 text-[12px] text-ink-3">{t("onboarding.cadenceLabel")}</div>
                <div className="flex flex-wrap gap-1.5">
                  {["Every 6h", "Every 12h", "Every 24h", "Every 48h", "Every 72h"].map((c) => (
                    <Pill key={c} active={cadence === c} onClick={() => setCadence(c)}>
                      {t(`onboarding.cadence.${c}`)}
                    </Pill>
                  ))}
                </div>
              </div>
              <div>
                <div className="mb-1 text-[12px] text-ink-3">{t("onboarding.scoringLabel")}</div>
                <div className="flex flex-wrap gap-1.5">
                  <Pill
                    active={scoringMode === "llm"}
                    onClick={() => setScoringMode("llm")}
                    data-testid="ob-scoring-llm"
                  >
                    {t("onboarding.scoringLlm")}
                  </Pill>
                  <Pill
                    active={scoringMode === "keyword"}
                    onClick={() => setScoringMode("keyword")}
                    data-testid="ob-scoring-keyword"
                  >
                    {t("onboarding.scoringKeyword")}
                  </Pill>
                </div>
                <div className="mt-1 text-[11.5px] text-ink-4">{t("onboarding.scoringHint")}</div>
              </div>
            </div>
          ) : step === 2 ? (
            <div className="space-y-4">
              <h1 className="text-[18px] font-semibold text-ink">{t("onboarding.titleProvider")}</h1>
              <p className="text-[13px] text-ink-3">
                {t("onboarding.providerIntro")}
                <InfoDot label={t("onboarding.verifyWhyLabel")}>
                  {t("onboarding.verifyWhyBody")}
                </InfoDot>
              </p>
              <div className="space-y-2">
                {PROVIDERS.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => {
                      setProvider(p.id);
                      setProviderInput("");
                      setVerifyState("idle");
                      setVerifyError("");
                      setVerifyStatus(null);
                      setVerifyDetail("");
                    }}
                    data-testid={`provider-${p.id}`}
                    className={
                      "flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left " +
                      (provider === p.id
                        ? "border-accent bg-accent-wash"
                        : "border-border bg-surface hover:bg-surface-2")
                    }
                  >
                    <span
                      className={
                        "grid h-4 w-4 place-items-center rounded-full border " +
                        (provider === p.id ? "border-accent" : "border-border-2")
                      }
                    >
                      {provider === p.id ? <span className="h-2 w-2 rounded-full bg-accent" /> : null}
                    </span>
                    <span className="flex-1">
                      <span className="block text-[13px] font-medium text-ink">{t(p.label)}</span>
                      <span className="block text-[11.5px] text-ink-3">{t(p.hint)}</span>
                    </span>
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2">
                {needsInput ? (
                  <input
                    type={provider === "local" ? "text" : "password"}
                    value={providerInput}
                    data-testid="api-key"
                    onChange={(e) => {
                      setProviderInput(e.target.value);
                      setVerifyState("idle");
                    }}
                    placeholder={
                      provider === "local" ? "http://localhost:11434" : t("onboarding.pasteKey")
                    }
                    className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
                  />
                ) : (
                  <span className="flex-1 text-[12px] text-ink-3">
                    {t(CLI_PROVIDERS[provider]?.verifyHint ?? "onboarding.noKeyNeeded")}
                  </span>
                )}
                <button
                  onClick={() => void verify()}
                  data-testid="verify-btn"
                  disabled={!canVerify || verifyState === "verifying"}
                  className={
                    "rounded-md border px-3 py-2 text-[12.5px] font-medium " +
                    (canVerify && verifyState !== "verifying"
                      ? "border-accent bg-accent text-white hover:bg-accent-ink"
                      : "cursor-not-allowed border-border bg-surface-3 text-ink-4")
                  }
                >
                  {verifyState === "verifying"
                    ? t("onboarding.verifying")
                    : verifyState === "verified"
                      ? t("onboarding.verified")
                      : t("onboarding.verify")}
                </button>
              </div>
              {verifyState === "verified" ? (
                <p className="text-[12px] text-good" data-testid="verify-ok">
                  {verifyDetail
                    ? t("onboarding.verifiedDetail", { detail: verifyDetail })
                    : t("onboarding.verifiedOk")}
                </p>
              ) : null}
              {verifyState === "failed" && verifyStatus === "not_logged_in" ? (
                <div
                  className="rounded-md border border-warn-2 bg-warn-wash p-2.5 text-[12px] text-warn"
                  data-testid="verify-login-needed"
                >
                  <div className="mb-2">
                    {t("onboarding.cliNotLoggedIn", {
                      cli: t(CLI_PROVIDERS[provider]?.name ?? "onboarding.cliFallback"),
                    })}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => openLoginTerminal(CLI_PROVIDERS[provider]?.loginCli)}
                      data-testid="claude-login-btn"
                      className="rounded-md border border-accent bg-accent px-2.5 py-1 text-[12px] font-medium text-white hover:bg-accent-ink"
                    >
                      {t(CLI_PROVIDERS[provider]?.loginLabel ?? "onboarding.loginFallback")}
                    </button>
                    <button
                      onClick={() => void verify()}
                      className="rounded-md border border-border px-2.5 py-1 text-[12px] text-ink"
                    >
                      {t("onboarding.verify")}
                    </button>
                  </div>
                </div>
              ) : verifyState === "failed" && verifyStatus === "not_found" ? (
                <div
                  className="rounded-md border border-bad/40 bg-bad-wash p-2.5 text-[12px] text-bad"
                  data-testid="verify-not-found"
                >
                  <div className="mb-2">
                    <Trans
                      i18nKey="onboarding.cliNotFound"
                      values={{
                        cli: t(CLI_PROVIDERS[provider]?.name ?? "onboarding.cliFallback"),
                        name: t(CLI_PROVIDERS[provider]?.installName ?? "onboarding.installFallback"),
                      }}
                      components={{
                        lnk: (
                          <a
                            href={CLI_PROVIDERS[provider]?.installUrl ?? "#"}
                            target="_blank"
                            rel="noreferrer"
                            className="underline"
                          />
                        ),
                      }}
                    />
                  </div>
                  <button onClick={() => void verify()} className="underline">
                    {t("onboarding.retry")}
                  </button>
                </div>
              ) : verifyState === "failed" ? (
                <div
                  className="rounded-md border border-bad/40 bg-bad-wash p-2 text-[12px] text-bad"
                  data-testid="verify-error"
                >
                  {verifyError}{" "}
                  <button onClick={() => void verify()} className="underline">
                    {t("onboarding.retry")}
                  </button>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="space-y-4 text-center">
              <div className="text-[40px]">🎉</div>
              <h1 className="text-[20px] font-semibold text-ink">{t("onboarding.titleAllSet")}</h1>
              <p className="text-[13px] text-ink-3">{t("onboarding.allSetIntro")}</p>
              <ul className="mx-auto max-w-xs space-y-1 text-left text-[12.5px] text-ink-2">
                <li>· {t("onboarding.allSetAddUrl")}</li>
                <li>· {t("onboarding.allSetResume")}</li>
                <li>· {t("onboarding.allSetSettings")}</li>
              </ul>
              {finishError ? (
                <div
                  className="rounded-md border border-bad/40 bg-bad-wash p-2 text-left text-[12px] text-bad"
                  data-testid="finish-error"
                >
                  {finishError}
                </div>
              ) : null}
            </div>
          )}
        </div>

        {/* Footer nav */}
        <div className="mt-6 flex items-center gap-2 border-t border-border pt-4">
          {step > 0 && step < STEP_LABELS.length - 1 ? (
            <button
              onClick={() => setStep((s) => s - 1)}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              {t("onboarding.back")}
            </button>
          ) : null}
          <div className="ml-auto">
            {step < STEP_LABELS.length - 1 ? (
              <button
                onClick={() => setStep((s) => s + 1)}
                disabled={!canContinue}
                data-testid="onboarding-continue"
                className={
                  "rounded-md px-4 py-1.5 text-[12.5px] font-medium " +
                  (canContinue
                    ? "bg-accent text-white hover:bg-accent-ink"
                    : "cursor-not-allowed bg-surface-3 text-ink-4")
                }
              >
                {t("onboarding.continue")}
              </button>
            ) : (
              <button
                onClick={() => void finish()}
                disabled={finishing}
                data-testid="onboarding-finish"
                className={
                  "rounded-md px-4 py-1.5 text-[12.5px] font-medium text-white " +
                  (finishing ? "cursor-not-allowed bg-surface-3 text-ink-4" : "bg-accent hover:bg-accent-ink")
                }
              >
                {finishing ? t("onboarding.finishing") : t("onboarding.finish")}
              </button>
            )}
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}

function Pill({
  active,
  onClick,
  children,
  "data-testid": testid,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <button
      onClick={onClick}
      data-testid={testid}
      className={
        "h-7 rounded-full border px-2.5 text-[11.5px] " +
        (active ? "border-accent bg-accent text-white" : "border-border-2 bg-surface text-ink-2 hover:bg-surface-3")
      }
    >
      {children}
    </button>
  );
}

function ChipField({
  label,
  testid,
  items,
  input,
  setInput,
  onAdd,
  onRemove,
  placeholder,
  hint,
}: {
  label: string;
  testid: string;
  items: string[];
  input: string;
  setInput: (v: string) => void;
  onAdd: () => void;
  onRemove: (v: string) => void;
  placeholder: string;
  hint?: string;
}) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="mb-1 text-[12px] text-ink-3">{label}</div>
      {hint ? <div className="mb-1.5 text-[11px] text-ink-4">{hint}</div> : null}
      <div className="flex flex-wrap gap-1.5">
        {items.map((it) => (
          <span
            key={it}
            className="inline-flex items-center gap-1 rounded-full bg-accent-wash px-2 py-0.5 text-[11.5px] text-accent-ink"
          >
            {it}
            <button
              onClick={() => onRemove(it)}
              aria-label={t("onboarding.removeChip", { value: it })}
              className="text-accent-ink/70"
            >
              ×
            </button>
          </span>
        ))}
        <input
          value={input}
          data-testid={`${testid}-input`}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Comma adds too — parity with the Job-finder-preferences chip
            // fields (maintainer 2026-07-22 #6).
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              onAdd();
            }
          }}
          placeholder={placeholder}
          className="min-w-[160px] flex-1 rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
        />
      </div>
    </div>
  );
}
