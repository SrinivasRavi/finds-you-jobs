// Settings (US-SET / §13) — Automation-on-Save, LLM providers + per-operation
// engine routing, the LinkedIn networking risk toggle w/ warning copy + ack,
// observability, appearance (theme). Ports settings*.html (product sections
// only — the prototype's purple "internal UI testing" mockups are not product).

import { useState } from "react";

import { api } from "../api/index";
import {
  useConnectLinkedIn,
  useDeleteDiscoveryCredential,
  useDeleteEngine,
  useDisconnectLinkedIn,
  useDiscoveryCredentials,
  useDiscoverySources,
  useLinkedinSearch,
  useLinkedInSession,
  usePrompts,
  useResetPrompt,
  useResumeLinkedIn,
  useSaveDiscoveryCredential,
  useSaveEngine,
  useSetPrompt,
  useSetLinkedInTier,
  useSettings,
  useToggleDiscoverySource,
  useUpdateSettings,
  useValidateLinkedIn,
  useVerifyEngine,
} from "../api/queries";
import type {
  EngineVerifyResult,
  LinkedInSessionState,
  OperationKind,
  PromptSetting,
  RescorePreview,
  Settings as SettingsT,
} from "../api/types";
import { Trans, useTranslation } from "react-i18next";

import { Icon, type IconName } from "../shell/icons";
import { InfoDot } from "../shell/InfoDot";
import { LanguageSelect } from "../shell/LanguageSelect";
import { RescoreAiDialog } from "../shell/RescoreAiDialog";
import { type ThemeMode, useThemeMode } from "../shell/theme";

const NETWORKING_WARNING = "settingsPage.referral.warning";

// LinkedIn Job Search breaks ToS by SCRAPING listings (not messaging) — its own
// justification: one-off + small default batch, so it reads as ordinary browsing.
const JOB_SEARCH_WARNING = "settingsPage.linkedinSearch.warning";

// Muted warn styling (2026-07-23): the dark-theme `warn-wash` (#78350f) reads as
// a loud brown; a light amber tint is calmer and consistent across every tab.
const MUTED_WARN_BOX = "rounded-lg border border-warn/30 bg-warn/5 text-warn";
const MUTED_WARN_PILL =
  "inline-flex cursor-help items-center gap-1 rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-[10px] font-semibold text-warn";

// The two LinkedIn features (Referral Outreach, LinkedIn job search) both drive
// your logged-in session and both break LinkedIn's ToS — same hazard marker,
// same shared session, separate opt-ins.
const LINKEDIN_HAZARD_TIP = "settingsPage.linkedinHazardTip";

function ExperimentalHazard() {
  const { t } = useTranslation();
  return (
    <span data-testid="experimental-hazard" title={t(LINKEDIN_HAZARD_TIP)} className={MUTED_WARN_PILL}>
      <span aria-hidden="true">⚠</span> {t("settingsPage.experimental")}
    </span>
  );
}

// The short warn-tinted risk line + an "i" to the full text — replaces the wall
// of warning copy on both LinkedIn opt-ins (2026-07-23: brief in place, detail
// one click away). `detail` differs per feature (messaging vs scraping).
function LinkedInRiskLine({ detail }: { detail: string }) {
  const { t } = useTranslation();
  return (
    <div className={"flex items-start gap-1 px-3 py-2 text-[11.5px] leading-relaxed " + MUTED_WARN_BOX}>
      <span>{t("settingsPage.riskLine")}</span>
      <InfoDot label={t("settingsPage.riskDetailLabel")}>{detail}</InfoDot>
    </div>
  );
}

// OTLP headers (audit P2-3) — the wire shape (`observability/config.py`'s
// `otlp_headers`) is a flat string dict; the Settings input edits it as a
// single "key1=val1,key2=val2" field, converted at the UI boundary only.
function otlpHeadersToText(headers: Record<string, string>): string {
  return Object.entries(headers)
    .map(([k, v]) => `${k}=${v}`)
    .join(",");
}

function otlpHeadersFromText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const pair of text.split(",")) {
    const idx = pair.indexOf("=");
    if (idx <= 0) continue;
    const key = pair.slice(0, idx).trim();
    const value = pair.slice(idx + 1).trim();
    if (key) out[key] = value;
  }
  return out;
}

function Toggle({
  on,
  onChange,
  testid,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  testid?: string;
}) {
  return (
    <button
      role="switch"
      aria-checked={on}
      data-testid={testid}
      onClick={() => onChange(!on)}
      className={
        "relative inline-block h-5 w-9 shrink-0 rounded-full transition-colors " +
        (on ? "bg-accent" : "bg-border-2")
      }
    >
      <span
        className={
          "absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all " +
          (on ? "left-[18px]" : "left-0.5")
        }
      />
    </button>
  );
}

// Three-way theme selector (FR-SET-09): Light / Dark / Follow system. The
// persisted mode wins; "system" resolves through prefers-color-scheme live.
const THEME_MODES: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "appearance.light" },
  { value: "dark", label: "appearance.dark" },
  { value: "system", label: "appearance.system" },
];

function ThemeModeToggle({
  mode,
  onChange,
}: {
  mode: ThemeMode;
  onChange: (m: ThemeMode) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="inline-flex overflow-hidden rounded-7 border border-border text-[12px]">
      {THEME_MODES.map((m) => (
        <button
          key={m.value}
          data-testid={`theme-mode-${m.value}`}
          aria-pressed={mode === m.value}
          onClick={() => onChange(m.value)}
          className={
            "px-2.5 py-1 " +
            (mode === m.value ? "bg-accent text-white" : "bg-surface text-ink-2 hover:bg-surface-3")
          }
        >
          {t(m.label)}
        </button>
      ))}
    </div>
  );
}

// Scoring batch cap presets (audit P1-1): the scheduler's `score_new` tick
// scores every unscored job by default (0 = uncapped) — a large first scan can
// burn a lot of LLM cost in one tick. These presets are a UI convenience over
// the same `thresholds.score_new_batch` the planner already reads
// (sidecar/app/scheduler/planner.py); the planner has read this since it
// shipped, this control is the missing writer.
const SCORE_BATCH_PRESETS: { value: number; label: string }[] = [
  { value: 0, label: "settingsPage.scoring.uncapped" },
  { value: 10, label: "10" },
  { value: 25, label: "25" },
  { value: 50, label: "50" },
];

function ScoreBatchCapControl({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="inline-flex overflow-hidden rounded-7 border border-border text-[12px]">
      {SCORE_BATCH_PRESETS.map((p) => (
        <button
          key={p.value}
          data-testid={`score-batch-cap-${p.value === 0 ? "uncapped" : p.value}`}
          aria-pressed={value === p.value}
          onClick={() => onChange(p.value)}
          className={
            "px-2.5 py-1 " +
            (value === p.value ? "bg-accent text-white" : "bg-surface text-ink-2 hover:bg-surface-3")
          }
        >
          {p.value === 0 ? t(p.label) : p.label}
        </button>
      ))}
    </div>
  );
}

// Discovery sources (maintainer directive 2026-07-18): one checkbox per
// scraper source family, all ON by default — pure opt-out. Lets a user drop a
// family that yields nothing for their role/location, and lets source efficacy
// be tested in isolation. Grouped by kind so the list of 18+ reads at a glance.
// Each section title carries its own master checkbox (2026-07-18 #5) that
// flips the whole section in one atomic POST; Apify actors are their own
// section (`sectionOf` routes their rows past the kind grouping).
const SOURCE_KIND_GROUPS: { kind: string; heading: string; blurb: string }[] = [
  {
    kind: "ats",
    heading: "settingsPage.sources.ats.heading",
    blurb: "settingsPage.sources.ats.blurb",
  },
  {
    kind: "board",
    heading: "settingsPage.sources.board.heading",
    blurb: "settingsPage.sources.board.blurb",
  },
  {
    kind: "search",
    heading: "settingsPage.sources.search.heading",
    blurb: "settingsPage.sources.search.blurb",
  },
  {
    kind: "apify",
    heading: "settingsPage.sources.apify.heading",
    blurb: "settingsPage.sources.apify.blurb",
  },
  {
    kind: "fallback",
    heading: "settingsPage.sources.fallback.heading",
    blurb: "settingsPage.sources.fallback.blurb",
  },
];

/** Which Settings section a catalog row belongs to. The Apify family row and
 *  its per-actor rows form their own section regardless of catalog kind. */
function sectionOf(s: { id: string; kind: string }): string {
  return s.id === "apify" || s.id.startsWith("apify:") ? "apify" : s.kind;
}

// BYO-key rows (Apify / Brave): a key input per provider, sealed at rest
// sidecar-side; saving the Apify key seeds its actor sources (Naukri/Indeed/
// Seek/LinkedIn deep-JD), saving Brave seeds the meta-search source.
function CredentialRow({ id, label, hint }: { id: string; label: string; hint: string }) {
  const { t } = useTranslation();
  const { data: creds } = useDiscoveryCredentials();
  const save = useSaveDiscoveryCredential();
  const remove = useDeleteDiscoveryCredential();
  const [draft, setDraft] = useState("");
  const row = creds?.find((c) => c.id === id);
  if (!row) return null;
  return (
    <div className="flex items-center gap-3" data-testid={`discovery-credential-${id}`}>
      <div className="flex-1">
        <div className="text-[13px] font-medium text-ink">{label}</div>
        <div className="text-[12px] text-ink-3">{hint}</div>
      </div>
      {row.has_key ? (
        <>
          <span className="font-mono text-[11px] text-ink-3">{row.key_hint ?? "•••"}</span>
          <button
            type="button"
            data-testid={`discovery-credential-remove-${id}`}
            onClick={() => remove.mutate(id)}
            className="rounded-md border border-border bg-surface px-2.5 py-1 text-[12px] text-ink-2 hover:border-border-2"
          >
            {t("settingsPage.sources.keys.remove")}
          </button>
        </>
      ) : (
        <>
          <input
            type="password"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={t("settingsPage.sources.keys.keyPlaceholder")}
            data-testid={`discovery-credential-input-${id}`}
            className="h-[30px] w-44 rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink placeholder:text-ink-4"
          />
          <button
            type="button"
            disabled={!draft.trim() || save.isPending}
            data-testid={`discovery-credential-save-${id}`}
            onClick={() =>
              save.mutate({ id, key: draft.trim() }, { onSuccess: () => setDraft("") })
            }
            className="rounded-md border border-accent bg-accent px-2.5 py-1 text-[12px] font-medium text-white hover:bg-accent-ink disabled:opacity-50"
          >
            {t("settingsPage.sources.keys.save")}
          </button>
        </>
      )}
    </div>
  );
}

/** The master checkbox in a section title — checked when every row in the
 *  section is on, unchecked when every row is off, indeterminate when mixed. */
function SectionMasterCheckbox({
  checked,
  indeterminate,
  onChange,
  testid,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: (enabled: boolean) => void;
  testid: string;
}) {
  const { t } = useTranslation();
  return (
    <input
      type="checkbox"
      checked={checked}
      ref={(el) => {
        if (el) el.indeterminate = indeterminate;
      }}
      onChange={(e) => onChange(e.target.checked)}
      data-testid={testid}
      title={t("settingsPage.sources.sectionToggleTitle")}
    />
  );
}

function DiscoverySourcesSection() {
  const { t } = useTranslation();
  const { data: sources } = useDiscoverySources();
  const toggle = useToggleDiscoverySource();
  if (!sources) return null;
  return (
    <div className="space-y-4" data-testid="discovery-sources">
      {SOURCE_KIND_GROUPS.map(({ kind, heading, blurb }) => {
        const isApify = kind === "apify";
        const sectionRows = sources.filter((s) => sectionOf(s) === kind);
        // Apify: the family row IS the section master; list only actor rows.
        const rows = isApify ? sectionRows.filter((s) => s.id !== "apify") : sectionRows;
        const family = isApify ? sectionRows.find((s) => s.id === "apify") : undefined;
        if (sectionRows.length === 0) return null;
        const allOn = rows.length > 0 && rows.every((s) => s.enabled);
        const anyOn = rows.some((s) => s.enabled);
        const masterChecked = isApify ? Boolean(family?.enabled) : allOn;
        const masterMixed = isApify
          ? Boolean(family?.enabled) && rows.length > 0 && !allOn
          : anyOn && !allOn;
        return (
          <div key={kind} className="space-y-1.5">
            <label className="flex cursor-pointer items-center gap-2">
              <SectionMasterCheckbox
                checked={masterChecked}
                indeterminate={masterMixed}
                testid={`source-section-toggle-${kind}`}
                onChange={(enabled) =>
                  isApify
                    ? toggle.mutate({ id: "apify", enabled })
                    : toggle.mutate({ ids: rows.map((s) => s.id), enabled })
                }
              />
              <span className="text-[12px] font-semibold text-ink-2">
                {t(heading)}
              </span>
            </label>
            <div className="text-[11.5px] text-ink-4">{t(blurb)}</div>
            {isApify && rows.length === 0 ? (
              <div className="text-[11.5px] text-ink-4">
                {t("settingsPage.sources.apifyEmpty")}
              </div>
            ) : null}
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 pt-1 pl-6">
              {rows.map((s) => (
                <label
                  key={s.id}
                  className="flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2"
                  data-testid={`source-toggle-${s.id}`}
                >
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    onChange={(e) => toggle.mutate({ id: s.id, enabled: e.target.checked })}
                  />
                  <span className={s.enabled ? "" : "text-ink-4"}>{s.label}</span>
                  {s.entries > 0 ? (
                    <span className="text-[11px] text-ink-4">
                      {t("settingsPage.sources.boardCount", { count: s.entries })}
                    </span>
                  ) : null}
                </label>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Bring-your-own-key scraper credentials — split out of the sources list into
// its own Section card (maintainer 2026-07-23: "Provide your API Keys").
function DiscoveryKeysSection() {
  const { t } = useTranslation();
  return (
    <div className="space-y-3" data-testid="discovery-keys">
      <p className="text-[11.5px] text-ink-4">
        {t("settingsPage.sources.keys.intro")}
      </p>
      <CredentialRow
        id="apify"
        label={t("settingsPage.sources.keys.apifyLabel")}
        hint={t("settingsPage.sources.keys.apifyHint")}
      />
      <CredentialRow
        id="brave"
        label={t("settingsPage.sources.keys.braveLabel")}
        hint={t("settingsPage.sources.keys.braveHint")}
      />
    </div>
  );
}

// `title` is optional: a pane whose header already names the content (e.g.
// Appearance) renders the card alone instead of repeating itself.
function Section({
  title,
  titleExtra,
  children,
}: {
  title?: string;
  titleExtra?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      {title ? (
        <div className="flex items-center gap-2">
          <h2 className="text-[13px] font-semibold text-ink">
            {title}
          </h2>
          {titleExtra}
        </div>
      ) : null}
      <div className="rounded-xl border border-border bg-surface p-4">{children}</div>
    </section>
  );
}

// ─── Contact & data lifecycle (FR-SYS-06 / FR-NW-15) ────────────────────────
// One editable row per configurable window. `unit` is days unless noted; a
// blank/zero input falls back to the stored value (the backend clamps too).
function LifecycleRow({
  label,
  hint,
  unit,
  value,
  onChange,
  testid,
}: {
  label: string;
  hint: string;
  unit: string;
  value: number;
  onChange: (v: number) => void;
  testid: string;
}) {
  return (
    <div className="flex items-center gap-3" data-testid={`lifecycle-${testid}-row`}>
      <div className="flex-1">
        <div className="text-[13px] font-medium text-ink">{label}</div>
        <div className="text-[12px] text-ink-3">{hint}</div>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          min={1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value) || 0)}
          data-testid={`lifecycle-${testid}`}
          className="w-20 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink"
        />
        <span className="text-[11.5px] text-ink-3">{unit}</span>
      </div>
    </div>
  );
}

export function LifecycleSection({
  settings,
  patch,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  const lc = settings.lifecycle;
  // Merge-patch a single field so unrelated windows aren't clobbered (mirrors the
  // observability patch shape — the mock/real clients replace the whole object).
  const set = (k: keyof SettingsT["lifecycle"]) => (v: number) =>
    patch({ lifecycle: { ...lc, [k]: v } });
  return (
    <Section title={t("settingsPage.lifecycle.title")}>
      <div className="space-y-4">
        <p className="text-[12px] text-ink-4">
          <Trans i18nKey="settingsPage.lifecycle.intro" components={{ em: <em /> }} />
        </p>
        <LifecycleRow
          label={t("settingsPage.lifecycle.engagementGhostedLabel")}
          hint={t("settingsPage.lifecycle.engagementGhostedHint")}
          unit={t("settingsPage.lifecycle.days")}
          value={lc.engagement_ghosted_days}
          onChange={set("engagement_ghosted_days")}
          testid="engagement-ghosted"
        />
        <LifecycleRow
          label={t("settingsPage.lifecycle.sentGhostedLabel")}
          hint={t("settingsPage.lifecycle.sentGhostedHint")}
          unit={t("settingsPage.lifecycle.days")}
          value={lc.sent_ghosted_days}
          onChange={set("sent_ghosted_days")}
          testid="sent-ghosted"
        />
        <LifecycleRow
          label={t("settingsPage.lifecycle.contactPurgeLabel")}
          hint={t("settingsPage.lifecycle.contactPurgeHint")}
          unit={t("settingsPage.lifecycle.days")}
          value={lc.contact_purge_days}
          onChange={set("contact_purge_days")}
          testid="contact-purge"
        />
        <LifecycleRow
          label={t("settingsPage.lifecycle.trashedJobsLabel")}
          hint={t("settingsPage.lifecycle.trashedJobsHint")}
          unit={t("settingsPage.lifecycle.days")}
          value={lc.trashed_jobs_purge_days}
          onChange={set("trashed_jobs_purge_days")}
          testid="trashed-jobs-purge"
        />
        <LifecycleRow
          label={t("settingsPage.lifecycle.archivedAppsLabel")}
          hint={t("settingsPage.lifecycle.archivedAppsHint")}
          unit={t("settingsPage.lifecycle.days")}
          value={lc.archived_applications_purge_days}
          onChange={set("archived_applications_purge_days")}
          testid="archived-apps-purge"
        />
        {settings.networking_enabled ? (
          <LifecycleRow
            label={t("settingsPage.lifecycle.syncCadenceLabel")}
            hint={t("settingsPage.lifecycle.syncCadenceHint")}
            unit={t("settingsPage.lifecycle.hours")}
            value={lc.contact_sync_cadence_hours}
            onChange={set("contact_sync_cadence_hours")}
            testid="sync-cadence"
          />
        ) : null}
      </div>
    </Section>
  );
}

// Mirrors the backend's claude-cli DEFAULT_MODEL (sidecar/modules/_shared/
// claude_engine.py) — shown as the effective model when a kind routes there.
const CLAUDE_CLI_DEFAULT_MODEL = "claude-opus-4-8";

// The subscription-CLI engine family — always routable, no EngineSettings row
// (mirrors the backend's engine_config.CLI_PROVIDERS). codex/agy run their
// CLI's own configured default model when the routing entry names none.
const CLI_ENGINE_OPTIONS = [
  { id: "claude-cli", label: "settingsPage.providers.cli.claudeLabel" },
  { id: "codex-cli", label: "settingsPage.providers.cli.codexLabel" },
  { id: "antigravity-cli", label: "settingsPage.providers.cli.antigravityLabel" },
];
const isCliEngine = (id: string) => CLI_ENGINE_OPTIONS.some((o) => o.id === id);

// ─── Engine routing + editable prompts (FR-SET-11) ──────────────────────────
// Each LLM operation is a collapsible row: header shows the engine/model
// summary + an "edited" badge; expanded reveals the engine selector (routed
// kinds only) and a monospace editor for that operation's system prompt (the
// module skill markdown), with Save/Reset. Collapsed by default so the large
// prompt text never overwhelms the page.

// One prompt's editor (routed model selector + full-height system-prompt
// textarea + Save/Reset). Rendered for the ACTIVE tab only; keyed by kind in the
// parent so switching tabs gives it fresh local draft state.
export function PromptEditor({
  prompt,
  settings,
  patch,
}: {
  prompt: PromptSetting;
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<string | null>(null);
  const [modelDraft, setModelDraft] = useState<string | null>(null);
  const setPrompt = useSetPrompt();
  const resetPrompt = useResetPrompt();

  const edited = prompt.override_md != null;
  const baseText = prompt.override_md ?? prompt.default_md;
  const text = draft ?? baseText;
  const dirty = text !== baseText;

  // The select picks the ENGINE; changing it clears the per-kind model so the
  // engine's own default applies.
  const route = settings.routing.find((r) => r.kind === prompt.kind);
  const engine = route?.engine || "claude-cli";
  const effectiveModel =
    route?.model ||
    (engine === "claude-cli"
      ? CLAUDE_CLI_DEFAULT_MODEL
      : isCliEngine(engine)
        ? t("settingsPage.prompts.cliDefaultModel")
        : settings.providers.find((p) => p.id === engine)?.default_model) ||
    t("settingsPage.prompts.providerDefault");
  const cliEngineLabel = CLI_ENGINE_OPTIONS.find((o) => o.id === engine)?.label;
  const engineLabel =
    (cliEngineLabel && t(cliEngineLabel)) ||
    settings.providers.find((p) => p.id === engine)?.label ||
    engine;
  const options = [
    ...CLI_ENGINE_OPTIONS.map((o) => ({ id: o.id, label: t(o.label) })),
    ...settings.providers.filter((p) => p.configured).map((p) => ({ id: p.id, label: p.label })),
  ];

  function save() {
    setPrompt.mutate({ kind: prompt.kind, markdown: text }, { onSuccess: () => setDraft(null) });
  }
  function reset() {
    if (!window.confirm(t("settingsPage.prompts.resetConfirm"))) return;
    resetPrompt.mutate(prompt.kind, { onSuccess: () => setDraft(null) });
  }

  return (
    <div className="flex flex-col gap-3">
      {prompt.routed ? (
        <div className="flex items-center gap-3">
          <span className="text-[11.5px] text-ink-3">{t("settingsPage.prompts.modelEngine")}</span>
          <select
            value={engine}
            data-testid={`route-${prompt.kind}`}
            onChange={(e) => {
              setModelDraft(null);
              patch({
                routing: [
                  ...settings.routing.filter((r) => r.kind !== prompt.kind),
                  { kind: prompt.kind as OperationKind, engine: e.target.value, model: "" },
                ],
              });
            }}
            className="rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink"
          >
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
          <input
            value={modelDraft ?? (route?.model || "")}
            placeholder={effectiveModel}
            data-testid={`route-${prompt.kind}-model`}
            title={t("settingsPage.prompts.modelTitle", { engineLabel, effectiveModel })}
            onChange={(e) => setModelDraft(e.target.value)}
            onBlur={() => {
              if (modelDraft == null || modelDraft === (route?.model || "")) return;
              patch({
                routing: [
                  ...settings.routing.filter((r) => r.kind !== prompt.kind),
                  { kind: prompt.kind as OperationKind, engine, model: modelDraft },
                ],
              });
              setModelDraft(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
            className="w-56 truncate rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink-2"
          />
        </div>
      ) : (
        <div className="text-[12px] text-ink-4">
          {t("settingsPage.prompts.noModel")}
        </div>
      )}
      <textarea
        value={text}
        spellCheck={false}
        data-testid={`prompt-textarea-${prompt.kind}`}
        onChange={(e) => setDraft(e.target.value)}
        className="h-[66vh] min-h-[360px] w-full resize-y rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-[12px] leading-relaxed text-ink"
      />
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-ink-4" data-testid={`prompt-chars-${prompt.kind}`}>
          {t("settingsPage.prompts.charCount", { n: text.length })}
          {edited
            ? t("settingsPage.prompts.overrideActive")
            : t("settingsPage.prompts.shippedDefault")}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={reset}
            disabled={!edited}
            data-testid={`prompt-reset-${prompt.kind}`}
            className="rounded-md border border-border px-2.5 py-1 text-[12px] text-ink-2 disabled:opacity-40"
          >
            {t("settingsPage.prompts.resetToDefault")}
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!dirty || !text.trim()}
            data-testid={`prompt-save-${prompt.kind}`}
            className="rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-white disabled:opacity-40"
          >
            {t("settingsPage.prompts.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

function EngineRoutingSection({
  settings,
  patch,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  const { data: prompts } = usePrompts();
  const list = prompts ?? [];
  const [active, setActive] = useState<string>("score");
  const current = list.find((p) => p.kind === active) ?? list[0];
  return (
    <div>
      {/* One-line tab bar — one tab per editable prompt. */}
      <div className="mb-4 flex flex-wrap gap-1.5 border-b border-border pb-2" role="tablist">
        {list.map((p) => {
          const on = current?.kind === p.kind;
          return (
            <button
              key={p.kind}
              type="button"
              role="tab"
              aria-selected={on}
              data-testid={`prompt-row-${p.kind}`}
              onClick={() => setActive(p.kind)}
              className={
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12.5px] font-medium " +
                (on ? "bg-accent-wash text-accent-ink" : "text-ink-2 hover:bg-surface-3")
              }
            >
              {p.title}
              {p.override_md != null ? (
                <span
                  data-testid={`prompt-edited-${p.kind}`}
                  title={t("settingsPage.prompts.customized")}
                  className="inline-block h-1.5 w-1.5 rounded-full bg-accent"
                />
              ) : null}
            </button>
          );
        })}
      </div>
      {current ? (
        <PromptEditor key={current.kind} prompt={current} settings={settings} patch={patch} />
      ) : null}
    </div>
  );
}

// AI Providers panel (FR-SET-06 / US-SET-07). The tile catalog is a static P1
// menu; configured/verified state is cross-referenced from settings.providers
// (the persisted EngineSettings rows) and "In use" from the routing map.
type ProviderCatalogEntry = {
  id: string;
  label: string;
  kind: "key" | "local";
  desc: string;
  modelChips?: string[];
  modelPlaceholder?: string;
};

const PROVIDER_CATALOG: ProviderCatalogEntry[] = [
  {
    id: "openrouter",
    label: "OpenRouter",
    kind: "key",
    desc: "settingsPage.providers.openrouterDesc",
    modelPlaceholder: "e.g. anthropic/claude-opus-4-8",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    kind: "key",
    desc: "settingsPage.providers.anthropicDesc",
    modelChips: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
  },
  {
    id: "openai",
    label: "OpenAI",
    kind: "key",
    desc: "settingsPage.providers.openaiDesc",
    modelChips: ["gpt-5", "gpt-5-mini", "gpt-4o", "gpt-4o-mini"],
  },
  {
    id: "local",
    label: "Local LLM",
    kind: "local",
    desc: "settingsPage.providers.localDesc",
    modelPlaceholder: "e.g. llama3.1:70b",
  },
];

const INPUT_CLS =
  "w-full rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink placeholder:text-ink-4";

// Subscription-CLI rows in the AI Providers panel (verify-only — no key, no
// persisted row; routing under "Engine routing & prompts" selects them).
const SUBSCRIPTION_CLIS: { id: string; label: string; desc: string }[] = [
  {
    id: "claude-cli",
    label: "settingsPage.providers.cli.claudeLabel",
    desc: "settingsPage.providers.cli.claudeDesc",
  },
  {
    id: "codex-cli",
    label: "settingsPage.providers.cli.codexLabel",
    desc: "settingsPage.providers.cli.codexDesc",
  },
  {
    id: "antigravity-cli",
    label: "settingsPage.providers.cli.antigravityLabel",
    desc: "settingsPage.providers.cli.antigravityDesc",
  },
];

function AIProvidersPanel({ settings }: { settings: SettingsT }) {
  const { t } = useTranslation();
  const verify = useVerifyEngine();
  const save = useSaveEngine();
  const del = useDeleteEngine();

  const [selected, setSelected] = useState<string>(
    () => settings.providers.find((p) => p.configured)?.id ?? PROVIDER_CATALOG[0].id,
  );
  const savedRow = settings.providers.find((p) => p.id === selected);
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(savedRow?.base_url ?? "");
  const [model, setModel] = useState(savedRow?.default_model ?? "");
  const [result, setResult] = useState<EngineVerifyResult | null>(null);
  // Per-CLI verify outcome/busy — independent of the BYOK verify flow above.
  const [cliResults, setCliResults] = useState<Record<string, EngineVerifyResult>>({});
  const [cliBusy, setCliBusy] = useState<string | null>(null);

  async function verifyCli(id: string) {
    setCliBusy(id);
    try {
      const res = await verify.mutateAsync({ provider: id });
      setCliResults((prev) => ({ ...prev, [id]: res }));
    } catch (e) {
      setCliResults((prev) => ({
        ...prev,
        [id]: {
          ok: false,
          status: "error",
          detail: e instanceof Error ? e.message : String(e),
          provider: id,
        },
      }));
    } finally {
      setCliBusy(null);
    }
  }

  const entry = PROVIDER_CATALOG.find((e) => e.id === selected) ?? PROVIDER_CATALOG[0];

  function select(id: string) {
    const row = settings.providers.find((p) => p.id === id);
    setSelected(id);
    setKey("");
    setBaseUrl(row?.base_url ?? "");
    setModel(row?.default_model ?? "");
    setResult(null);
  }

  const input = {
    provider: selected,
    key: key || undefined,
    base_url: baseUrl || undefined,
    default_model: model || undefined,
  };
  const inUse = settings.routing.some((r) => r.engine === selected);
  // The subscription CLIs are always routable (no saved key) — operations
  // default-route to claude-cli, so "nothing configured" is only a real
  // problem when an operation is routed to a BYOK provider with no saved key
  // (2026-07-23: the old blanket warning contradicted the "In use" CLI badge).
  const unconfiguredRouted = settings.routing.some(
    (r) =>
      !isCliEngine(r.engine) &&
      !settings.providers.find((p) => p.id === r.engine)?.configured,
  );

  return (
    <div className="space-y-4" data-testid="ai-providers-panel">
      {unconfiguredRouted && (
        <div data-testid="no-provider-warning" className={"p-3 text-[11.5px] " + MUTED_WARN_BOX}>
          {t("settingsPage.providers.unconfiguredWarning")}
        </div>
      )}

      {/* Tile grid */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {PROVIDER_CATALOG.map((e) => {
          const row = settings.providers.find((p) => p.id === e.id);
          return (
            <button
              key={e.id}
              data-testid={`provider-tile-${e.id}`}
              onClick={() => select(e.id)}
              // No highlight state on tiles (maintainer 2026-07-24 #2): the
              // config panel right below names the picked tile, and the
              // Configured / In use text says what's actually live — a lit
              // tile just read as "already active" when nothing was set.
              className="rounded-lg border border-border bg-surface p-3 text-left transition-colors hover:bg-surface-3"
            >
              <div className="text-[13px] font-medium text-ink">{e.label}</div>
              <div className={"mt-1 text-[10.5px] " + (row?.configured ? "text-good" : "text-ink-4")}>
                {row?.configured
                  ? t("settingsPage.providers.configured")
                  : t("settingsPage.providers.notSet")}
              </div>
            </button>
          );
        })}
      </div>

      {/* Config panel */}
      <div className="rounded-lg border border-border bg-surface-2 p-3" data-testid="provider-config-panel">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[13px] font-medium text-ink">{entry.label}</div>
          {inUse && (
            <span
              data-testid="engine-in-use"
              className="rounded-full bg-good-wash px-2 py-0.5 text-[10px] font-medium text-good"
            >
              {t("settingsPage.providers.inUse")}
            </span>
          )}
        </div>
        <p className="mt-1 text-[12px] text-ink-3">{t(entry.desc)}</p>

        {entry.kind === "local" ? (
          <div className="mt-3 space-y-2">
            <input
              data-testid="engine-base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://localhost:11434/v1"
              className={INPUT_CLS}
            />
            <input
              data-testid="engine-model-input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={entry.modelPlaceholder}
              className={INPUT_CLS}
            />
          </div>
        ) : (
          <div className="mt-3 space-y-2">
            <input
              data-testid="engine-key-input"
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={
                savedRow?.configured
                  ? t("settingsPage.providers.keySavedPlaceholder", {
                      hint: savedRow.key_hint ?? "•••",
                    })
                  : t("settingsPage.providers.keyPlaceholder")
              }
              className={INPUT_CLS}
            />
            {entry.modelChips ? (
              <div className="flex flex-wrap gap-1.5">
                {entry.modelChips.map((m) => (
                  <button
                    key={m}
                    data-testid={`model-chip-${m}`}
                    onClick={() => setModel(m)}
                    className={
                      "rounded-full border px-2 py-0.5 text-[11px] " +
                      (model === m
                        ? "border-accent bg-accent text-white"
                        : "border-border bg-surface text-ink-2 hover:bg-surface-3")
                    }
                  >
                    {m}
                  </button>
                ))}
              </div>
            ) : (
              <input
                data-testid="engine-model-input"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={entry.modelPlaceholder}
                className={INPUT_CLS}
              />
            )}
          </div>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            data-testid="engine-verify-btn"
            disabled={verify.isPending}
            onClick={() => verify.mutate(input, { onSuccess: setResult })}
            className="inline-flex h-[30px] items-center rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink hover:bg-surface-3 disabled:opacity-50"
          >
            {verify.isPending
              ? t("settingsPage.providers.verifying")
              : t("settingsPage.providers.verify")}
          </button>
          <button
            data-testid="engine-save-btn"
            onClick={() => save.mutate(input, { onSuccess: () => setKey("") })}
            className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
          >
            {t("settingsPage.providers.save")}
          </button>
          {savedRow && (
            <button
              data-testid="engine-delete-btn"
              onClick={() => del.mutate(selected)}
              className="inline-flex h-[30px] items-center rounded-md border border-transparent px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
            >
              {t("settingsPage.providers.remove")}
            </button>
          )}
        </div>

        {result && (
          <div
            data-testid="engine-verify-result"
            className={
              "mt-2 rounded-md border p-2 text-[11.5px] " +
              (result.ok
                ? "border-good-2 bg-good-wash text-good"
                : "border-bad-2 bg-bad-wash text-bad")
            }
          >
            {result.ok ? t("settingsPage.providers.verified") : result.detail}
          </div>
        )}
      </div>

      {/* Subscription CLIs — verify-only providers (no key, nothing persisted);
          route operations to one under Prompts & Models. */}
      <div
        className="rounded-lg border border-border bg-surface-2 p-3"
        data-testid="cli-providers-panel"
      >
        <div className="text-[13px] font-medium text-ink">
          {t("settingsPage.providers.clisTitle")}
        </div>
        <p className="mt-1 text-[12px] text-ink-3">
          {t("settingsPage.providers.clisIntro")}
        </p>
        <div className="mt-2 space-y-1.5">
          {SUBSCRIPTION_CLIS.map((c) => {
            const res = cliResults[c.id];
            const busy = cliBusy === c.id;
            return (
              <div
                key={c.id}
                data-testid={`cli-provider-${c.id}`}
                className="flex items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-2"
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-[12.5px] font-medium text-ink">{t(c.label)}</span>
                    {settings.routing.some((r) => r.engine === c.id) ? (
                      <span className="rounded-full bg-good-wash px-1.5 py-0.5 text-[9px] font-medium text-good">
                        {t("settingsPage.providers.inUse")}
                      </span>
                    ) : null}
                  </span>
                  <span className="block truncate text-[11px] text-ink-3">{t(c.desc)}</span>
                  {res ? (
                    <span
                      data-testid={`cli-verify-result-${c.id}`}
                      className={
                        "block truncate text-[11px] " + (res.ok ? "text-good" : "text-bad")
                      }
                    >
                      {res.detail}
                    </span>
                  ) : null}
                </span>
                <button
                  onClick={() => void verifyCli(c.id)}
                  disabled={busy}
                  data-testid={`cli-verify-${c.id}`}
                  className="rounded-md border border-border px-2.5 py-1 text-[12px] text-ink-2 hover:border-border-2 disabled:opacity-40"
                >
                  {busy
                    ? t("settingsPage.providers.verifying")
                    : res?.ok
                      ? t("settingsPage.providers.verifiedCheck")
                      : t("settingsPage.providers.verify")}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// OS-style Settings navigation (maintainer directive 2026-07-23): a left
// category rail + one focused pane per category, the way macOS/Windows Settings
// organize a large surface — instead of one long scroll where "Prompts" was
// invisible at the bottom. Each pane reuses the existing `Section` cards.
type SettingsCat =
  | "providers"
  | "prompts"
  | "discovery"
  | "networking"
  | "data"
  | "appearance";

const SETTINGS_CATS: {
  id: SettingsCat;
  label: string; // i18n key
  icon: IconName;
  blurb: string; // i18n key
}[] = [
  { id: "providers", label: "settingsNav.providers", icon: "settings", blurb: "settingsNav.providersBlurb" },
  { id: "prompts", label: "settingsNav.prompts", icon: "pencil", blurb: "settingsNav.promptsBlurb" },
  { id: "discovery", label: "settingsNav.discovery", icon: "search", blurb: "settingsNav.discoveryBlurb" },
  { id: "networking", label: "settingsNav.networking", icon: "share", blurb: "settingsNav.networkingBlurb" },
  { id: "data", label: "settingsNav.data", icon: "barChart", blurb: "settingsNav.dataBlurb" },
  { id: "appearance", label: "settingsNav.appearance", icon: "sun", blurb: "settingsNav.appearanceBlurb" },
];

function SettingsNav({
  active,
  onPick,
}: {
  active: SettingsCat;
  onPick: (c: SettingsCat) => void;
}) {
  const { t } = useTranslation();
  return (
    <nav
      aria-label={t("settingsPage.navAriaLabel")}
      data-testid="settings-nav"
      className="w-56 shrink-0 space-y-0.5 overflow-y-auto border-r border-border bg-surface p-3"
    >
      {SETTINGS_CATS.map((c) => {
        const on = c.id === active;
        return (
          <button
            key={c.id}
            type="button"
            onClick={() => onPick(c.id)}
            data-testid={`settings-nav-${c.id}`}
            aria-current={on ? "page" : undefined}
            className={
              "flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors " +
              (on ? "bg-accent-wash text-accent-ink" : "text-ink-2 hover:bg-surface-3")
            }
          >
            <span className={"mt-0.5 " + (on ? "text-accent" : "text-ink-3")}>
              <Icon name={c.icon} size={15} strokeWidth={2} />
            </span>
            <span className="min-w-0">
              <span className="block text-[13px] font-medium leading-tight">{t(c.label)}</span>
              <span className="block truncate text-[11px] leading-tight text-ink-4">
                {t(c.blurb)}
              </span>
            </span>
          </button>
        );
      })}
    </nav>
  );
}

export function Settings() {
  const { t } = useTranslation();
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const [themeMode, , setThemeMode] = useThemeMode();
  const [cat, setCat] = useState<SettingsCat>("providers");
  const [ack, setAck] = useState(false);
  // Switching Scoring keyword → AI: the server never spends on its own, so
  // preview the cache misses and ask before any token goes out (maintainer
  // 2026-07-23). Jobs already AI-scored at the current resume are skipped.
  const [rescoreAsk, setRescoreAsk] = useState<RescorePreview | null>(null);

  if (!settings) return null;

  function patch(p: Partial<SettingsT>) {
    update.mutate(p);
  }

  function pickScoringMode(mode: SettingsT["scoring_mode"]) {
    const was = settings?.scoring_mode;
    update.mutate(
      { scoring_mode: mode },
      {
        onSuccess: () => {
          if (mode === "llm" && was === "keyword") {
            void api.rescorePreview().then((preview) => {
              if (preview.to_score > 0) setRescoreAsk(preview);
            });
          }
        },
      },
    );
  }

  const active = SETTINGS_CATS.find((c) => c.id === cat) ?? SETTINGS_CATS[0];
  return (
    <>
      <header className="flex min-h-[48px] items-center border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">{t("nav.settings")}</h1>
      </header>
      <main className="flex min-h-0 flex-1 overflow-hidden">
        <SettingsNav active={cat} onPick={setCat} />
        <div className="flex-1 overflow-y-auto">
        {/* All panes share one comfortable width (2026-07-23: the full-width
            Prompts pane read as too spread out). Less side-padding than before. */}
        <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-5">
          <header className="space-y-1">
            <h2 className="text-[18px] font-semibold text-ink">{t(active.label)}</h2>
            <p className="text-[13px] text-ink-3">{t(active.blurb)}</p>
          </header>

          {cat === "discovery" && (
          <div className="space-y-8">
          {/* Discovery sources — per-family opt-out toggles (2026-07-18).
              First section on purpose: discovery is the first stage of the
              pipeline everything below feeds on. */}
          <Section title={t("settingsPage.sources.title")}>
            <DiscoverySourcesSection />
          </Section>

          {/* BYO scraper keys — their own card so the sources list above stays
              a pure pick-list (maintainer 2026-07-23). */}
          <Section title={t("settingsPage.sources.keys.title")}>
            <DiscoveryKeysSection />
          </Section>

          {/* LinkedIn job search sits above Scoring (maintainer 2026-07-23) — an
              experimental discovery source with its own ToS opt-in, sharing the
              LinkedIn session with Referral Outreach. */}
          <LinkedInJobSearchSection settings={settings} patch={patch} />

          {/* Scoring: a scanned job is scored before anything else happens to it.
              Every scanned job is scored; the choice is HOW. AI failures fall
              back to a grey keyword score (retry in Logs). */}
          <Section title={t("settingsPage.scoring.title")}>
            <div className="space-y-4">
              <div>
                <div className="flex items-center text-[13px] font-medium text-ink">
                  {t("settingsPage.scoring.howTitle")}
                  <InfoDot label={t("settingsPage.scoring.fallbackLabel")}>
                    {t("settingsPage.scoring.fallbackInfo")}
                  </InfoDot>
                </div>
                <div className="mb-2 text-[12px] text-ink-3">
                  {t("settingsPage.scoring.howHint")}
                </div>
                <div className="flex flex-col gap-1.5" data-testid="scoring-mode-picker">
                  {(
                    [
                      ["llm", "settingsPage.scoring.modeLlm"],
                      ["keyword", "settingsPage.scoring.modeKeyword"],
                    ] as const
                  ).map(([mode, label]) => (
                    <button
                      key={mode}
                      type="button"
                      data-testid={`scoring-mode-${mode}`}
                      data-on={settings.scoring_mode === mode}
                      onClick={() => pickScoringMode(mode)}
                      className={
                        "rounded-md border px-3 py-2 text-left text-[12.5px] " +
                        (settings.scoring_mode === mode
                          ? "border-accent bg-accent-wash text-accent-ink"
                          : "border-border-2 bg-surface text-ink-2 hover:bg-surface-3")
                      }
                    >
                      {t(label)}
                    </button>
                  ))}
                </div>
              </div>
              {settings.scoring_mode === "llm" ? (
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <div className="flex items-center text-[13px] font-medium text-ink">
                      {t("settingsPage.scoring.batchCap")}
                      <InfoDot label={t("settingsPage.scoring.batchCap")}>
                        {t("settingsPage.scoring.batchCapInfo")}
                      </InfoDot>
                    </div>
                    <div className="text-[12px] text-ink-3">
                      {t("settingsPage.scoring.batchCapHint")}
                    </div>
                  </div>
                  <ScoreBatchCapControl
                    value={settings.score_new_batch}
                    onChange={(v) => patch({ score_new_batch: v })}
                  />
                </div>
              ) : null}
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="flex items-center text-[13px] font-medium text-ink">
                    {t("settingsPage.scoring.parallel")}
                    <InfoDot label={t("settingsPage.scoring.parallel")}>
                      {t("settingsPage.scoring.parallelInfo")}
                    </InfoDot>
                  </div>
                  <div className="text-[12px] text-ink-3">
                    {t("settingsPage.scoring.parallelHint")}
                  </div>
                </div>
                <div className="flex items-center gap-1.5" data-testid="llm-concurrency-row">
                  <select
                    value={String(settings.llm_concurrency)}
                    data-testid="llm-concurrency-select"
                    onChange={(e) => patch({ llm_concurrency: Number(e.target.value) })}
                    className="h-[30px] rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink"
                  >
                    {[2, 3, 4, 6, 8, 10, 12, 16, 20].map((n) => (
                      <option key={n} value={n}>
                        {t("settingsPage.scoring.atOnce", { n })}
                      </option>
                    ))}
                    <option value={0}>{t("settingsPage.scoring.unlimited")}</option>
                  </select>
                </div>
              </div>
            </div>
          </Section>

          {/* Automation on Save — split defaults (FR-SET-02): Resume ON, Cover ON.
              After Scoring in the workflow (maintainer 2026-07-23). */}
          <Section title={t("settingsPage.automation.title")}>
            <div className="space-y-4">
              <p className="text-[12px] text-ink-4">
                {t("settingsPage.automation.intro")}
                <InfoDot label={t("settingsPage.automation.perJobLabel")}>
                  {t("settingsPage.automation.perJobInfo")}
                </InfoDot>
              </p>
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="flex items-center text-[13px] font-medium text-ink">
                    {t("settingsPage.automation.resumeLabel")}
                    <InfoDot label={t("settingsPage.automation.resumeInfoLabel")}>
                      {t("settingsPage.automation.resumeInfo")}
                    </InfoDot>
                  </div>
                  <div className="text-[12px] text-ink-3">
                    {t("settingsPage.automation.resumeHint")}
                  </div>
                </div>
                <Toggle
                  on={settings.auto_resume_on_save}
                  onChange={(v) => patch({ auto_resume_on_save: v })}
                  testid="auto-resume-toggle"
                />
              </div>
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="text-[13px] font-medium text-ink">
                    {t("settingsPage.automation.coverLabel")}
                  </div>
                  <div className="text-[12px] text-ink-3">
                    {t("settingsPage.automation.coverHint")}
                  </div>
                </div>
                <Toggle
                  on={settings.auto_cover_on_save}
                  onChange={(v) => patch({ auto_cover_on_save: v })}
                  testid="auto-cover-toggle"
                />
              </div>
              {/* The prior repository's "Application form answers when I save a
                  job" (auto_prep_on_save) toggle is retired with the Save-time
                  prep op (docs/internal/applier.md §2) — the agentic Applier
                  reads the live form instead. */}
              {/* Referrals default — only when Referral Outreach is enabled
                  (it's the experimental, account-risk path). */}
              {settings.networking_enabled ? (
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <div className="flex items-center text-[13px] font-medium text-ink">
                      {t("settingsPage.automation.referralsLabel")}
                      <InfoDot label={t("settingsPage.automation.referralsInfoLabel")}>
                        {t("settingsPage.automation.referralsInfo")}
                      </InfoDot>
                    </div>
                    <div className="text-[12px] text-ink-3">
                      {t("settingsPage.automation.referralsHint")}
                    </div>
                  </div>
                  <Toggle
                    on={settings.auto_referrals_on_save}
                    onChange={(v) => patch({ auto_referrals_on_save: v })}
                    testid="auto-referrals-toggle"
                  />
                </div>
              ) : null}
            </div>
          </Section>

          </div>
          )}

          {/* The "Applications" settings category (P1 Applier statement +
              Application-profile editor) was removed 2026-07-23 (maintainer:
              no value in P1). The ApplicationProfileEditor component is parked
              below, unreferenced, for trivial restoration; the profile is still
              auto-extracted on master-save regardless. */}

          {cat === "providers" && (
          <div className="space-y-8">
          {/* LLM Providers (FR-SET-06 / US-SET-07) — tile grid + Verify + Save */}
          <Section title={t("settingsPage.providers.title")}>
            <AIProvidersPanel settings={settings} />
          </Section>
          </div>
          )}

          {cat === "prompts" && (
          <div className="space-y-8">
          {/* Per-operation engine routing + editable system prompts (FR-SET-11).
              Its own category so prompt editing is discoverable — the old long
              scroll buried it at the bottom (maintainer 2026-07-23). */}
          <Section title={t("settingsPage.prompts.title")}>
            <EngineRoutingSection settings={settings} patch={patch} />
          </Section>
          </div>
          )}

          {cat === "networking" && (
          <div className="space-y-8">

          {/* Referral Outreach risk toggle — the canonical feature name for the
              automated LinkedIn module (maintainer, 2026-07-10). The Networking
              tab (contact CRM + kanban + manual tracking) is ALWAYS available and
              carries no risk; this gates only the automated actions. This section
              is deliberately the feature's ONE reveal point (it is never
              advertised elsewhere), so the copy carries the full context. */}
          <Section title={t("settingsPage.referral.title")} titleExtra={<ExperimentalHazard />}>
            <div className="space-y-3">
              <p className="text-[12.5px] text-ink-2">
                {t("settingsPage.referral.intro")}
                <InfoDot label={t("settingsPage.referral.howLabel")}>
                  <Trans i18nKey="settingsPage.referral.howInfo" components={{ em: <em /> }} />
                </InfoDot>
              </p>
              <LinkedInRiskLine detail={t(NETWORKING_WARNING)} />
              <label className="flex items-start gap-2 text-[12px] font-medium text-ink-2">
                <input
                  type="checkbox"
                  checked={ack || settings.networking_enabled}
                  onChange={(e) => setAck(e.target.checked)}
                  data-testid="networking-ack"
                  className="mt-0.5"
                />
                {t("settingsPage.referral.ack")}
              </label>
              <div className="flex items-center gap-3">
                <div className="flex-1 text-[13px] font-medium text-ink">
                  {t("settingsPage.referral.enable")}
                </div>
                <Toggle
                  on={settings.networking_enabled}
                  onChange={(v) => {
                    if (v && !ack) return;
                    // Durable ack record (audit P2-5): the checkbox above is
                    // ephemeral local state (resets on disable); this timestamp
                    // persists to ui_state so re-opening Settings shows *when*
                    // the ToS risk was last accepted, not just the live toggle.
                    patch(
                      v
                        ? { networking_enabled: v, networking_ack_at: new Date().toISOString() }
                        : { networking_enabled: v },
                    );
                    if (!v) setAck(false);
                  }}
                  testid="networking-toggle"
                />
              </div>
              {settings.networking_ack_at ? (
                <div className="text-[11px] text-ink-4" data-testid="networking-ack-at">
                  {t("settingsPage.acknowledgedOn", {
                    date: new Date(settings.networking_ack_at).toLocaleDateString(),
                  })}
                </div>
              ) : null}
              {/* Step 2 — the LinkedIn session (US-SET-06) lives INSIDE this
                  experimental section (2026-07-17 dogfood: shown separately it
                  read as an unrelated, non-experimental setting and the user
                  never connected). Rendered only when the toggle is on. */}
              {settings.networking_enabled ? (
                <LinkedInSessionSection />
              ) : (
                <div className="text-[11.5px] text-ink-4">
                  {t("settingsPage.referral.lockedHint")}
                </div>
              )}
            </div>
          </Section>
          </div>
          )}

          {cat === "data" && (
          <div className="space-y-8">
          {/* Observability */}
          <Section title={t("settingsPage.observability.title")}>
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="flex items-center text-[13px] font-medium text-ink">
                    {t("settingsPage.observability.contentLogging")}
                    <InfoDot label={t("settingsPage.observability.contentLogging")}>
                      {t("settingsPage.observability.contentLoggingInfo")}
                    </InfoDot>
                  </div>
                  <div className="text-[12px] text-ink-3">
                    {t("settingsPage.observability.contentLoggingHint")}
                  </div>
                </div>
                <Toggle
                  on={settings.observability.content_logging}
                  onChange={(v) =>
                    patch({ observability: { ...settings.observability, content_logging: v } })
                  }
                  testid="content-logging-toggle"
                />
              </div>
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="flex items-center text-[13px] font-medium text-ink">
                    {t("settingsPage.observability.otlpExport")}
                    <InfoDot label={t("settingsPage.observability.otlpExport")}>
                      {t("settingsPage.observability.otlpExportInfo")}
                    </InfoDot>
                  </div>
                  <div className="text-[12px] text-ink-3">
                    {t("settingsPage.observability.otlpExportHint")}
                  </div>
                </div>
                <Toggle
                  on={settings.observability.otlp_enabled}
                  onChange={(v) =>
                    patch({ observability: { ...settings.observability, otlp_enabled: v } })
                  }
                  testid="otlp-toggle"
                />
              </div>
              {settings.observability.otlp_enabled ? (
                <div className="flex items-center gap-3" data-testid="otlp-endpoint-row">
                  <div className="flex-1 text-[13px] text-ink-2">
                    {t("settingsPage.observability.otlpEndpoint")}
                  </div>
                  <input
                    value={settings.observability.otlp_endpoint}
                    onChange={(e) =>
                      patch({
                        observability: { ...settings.observability, otlp_endpoint: e.target.value },
                      })
                    }
                    placeholder="https://otlp.example.com:4318/v1/traces"
                    className="w-64 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink placeholder:text-ink-4"
                  />
                </div>
              ) : null}
              {settings.observability.otlp_enabled ? (
                <div className="flex items-center gap-3" data-testid="otlp-headers-row">
                  <div className="flex-1 text-[13px] text-ink-2">
                    {t("settingsPage.observability.otlpHeaders")}
                  </div>
                  <input
                    value={otlpHeadersToText(settings.observability.otlp_headers)}
                    onChange={(e) =>
                      patch({
                        observability: {
                          ...settings.observability,
                          otlp_headers: otlpHeadersFromText(e.target.value),
                        },
                      })
                    }
                    placeholder="key1=val1,key2=val2"
                    className="w-64 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink placeholder:text-ink-4"
                  />
                </div>
              ) : null}
              <div className="flex items-center gap-3" data-testid="retention-days-row">
                <div className="flex-1">
                  <div className="text-[13px] text-ink-2">
                    {t("settingsPage.observability.retention")}
                  </div>
                  <div className="text-[12px] text-ink-3">
                    {t("settingsPage.observability.retentionHint")}
                  </div>
                </div>
                <input
                  type="number"
                  min={1}
                  value={settings.observability.retention_days}
                  onChange={(e) =>
                    patch({
                      observability: {
                        ...settings.observability,
                        retention_days: Number(e.target.value) || 0,
                      },
                    })
                  }
                  className="w-20 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink"
                />
              </div>
            </div>
          </Section>

          {/* Contact & data lifecycle (FR-SYS-06 / FR-NW-15) — configurable
              windows for kanban ghosting, purge, and the contact-sync cadence. */}
          <LifecycleSection settings={settings} patch={patch} />
          </div>
          )}

          {cat === "appearance" && (
          <div className="space-y-8">
          {/* Untitled card — the pane header already says "Appearance"
              (maintainer 2026-07-24 #5: stop repeating it). */}
          <Section>
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="text-[13px] font-medium text-ink">{t("appearance.theme")}</div>
                  <div className="text-[11.5px] text-ink-3">{t("appearance.themeHint")}</div>
                </div>
                <ThemeModeToggle mode={themeMode} onChange={setThemeMode} />
              </div>
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="text-[13px] font-medium text-ink">{t("appearance.language")}</div>
                  <div className="text-[11.5px] text-ink-3">{t("appearance.languageHint")}</div>
                </div>
                <LanguageSelect testid="language-select" />
              </div>
            </div>
          </Section>
          </div>
          )}
        </div>
        </div>
      </main>
      {rescoreAsk !== null ? (
        <RescoreAiDialog
          preview={rescoreAsk}
          reason="mode-switch"
          onClose={() => setRescoreAsk(null)}
        />
      ) : null}
    </>
  );
}

// LinkedIn session capture (US-SET-06 as-built). Divergence from the prototype's
// cookie-paste form: the maintainer directed a **headed-browser login** — click
// Connect, a real browser opens at LinkedIn's login page, you log in yourself
// (incl. 2FA; the password never touches finds-you-jobs), and we save the session
// cookies once the `li_at` auth cookie appears. Status chip + connected-as +
// expiry + Validate/Disconnect/Resume mirror settings-linkedin.html.

type PillVariant = { cls: string; dot: string; label: string };

// `label` is an i18n key — t()'d where the pill renders.
function statusPill(status: LinkedInSessionState["status"]): PillVariant {
  switch (status) {
    case "valid":
      return {
        cls: "bg-good-wash border-good-2 text-good",
        dot: "#1F9D55",
        label: "settingsPage.session.statusConnected",
      };
    case "connecting":
      return {
        cls: "bg-warn-wash border-warn-2 text-warn",
        dot: "#C5A24A",
        label: "settingsPage.session.statusConnecting",
      };
    case "backing_off":
      return {
        cls: "bg-bad-wash border-bad-2 text-bad",
        dot: "#B23A3A",
        label: "settingsPage.session.statusBackingOff",
      };
    case "expired":
      return {
        cls: "bg-bad-wash border-bad-2 text-bad",
        dot: "#B23A3A",
        label: "settingsPage.session.statusExpired",
      };
    default:
      return {
        cls: "bg-bad-wash border-bad-2 text-bad",
        dot: "#B23A3A",
        label: "settingsPage.session.statusDisconnected",
      };
  }
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

// Collapsible + SHARED (2026-07-23): one LinkedIn session drives both Referral
// Outreach and LinkedIn Job Search. Rendered in both places; because both read
// the same session query + hit the same backend, connecting or disconnecting in
// one reflects instantly in the other. Collapsed once connected to stay tidy.
function LinkedInSessionSection() {
  const { t } = useTranslation();
  const { data: session } = useLinkedInSession();
  const connect = useConnectLinkedIn();
  const disconnect = useDisconnectLinkedIn();
  const validate = useValidateLinkedIn();
  const resume = useResumeLinkedIn();
  const setTier = useSetLinkedInTier();
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);

  if (!session) return null;
  const status = session.status;
  const pill = statusPill(status);
  const connecting = status === "connecting" || connect.isPending;
  const connected = status === "valid";
  const open = openOverride ?? !connected; // expanded until connected, then tidy

  return (
    <div className="rounded-lg border border-border bg-surface-2 p-3" data-testid="linkedin-session-section">
      <button
        type="button"
        onClick={() => setOpenOverride(!open)}
        aria-expanded={open}
        data-testid="linkedin-session-toggle"
        className="flex w-full items-center gap-2 text-left"
      >
        <span className="w-3 text-ink-4">{open ? "▾" : "▸"}</span>
        <span className="text-[12px] font-semibold text-ink-3">
          {t("settingsPage.session.title")}
        </span>
        <span
          data-testid="linkedin-status-pill"
          className={
            "ml-auto inline-flex h-[20px] items-center gap-[5px] rounded-full border px-2 text-[11px] font-medium " +
            pill.cls
          }
        >
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: pill.dot }} />
          {t(pill.label)}
        </span>
      </button>
      {open ? (
        <div className="mt-3 space-y-4">
          <p className="text-[12.5px] text-ink-3">
            <Trans i18nKey="settingsPage.session.intro" components={{ strong: <strong /> }} />
            <InfoDot label={t("settingsPage.session.howLabel")}>
              {t("settingsPage.session.howInfo")}
            </InfoDot>
          </p>

          {/* Status details + actions */}
          <div className="rounded-lg border border-border bg-surface p-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[12px] text-ink-3">
              <dt>{t("settingsPage.session.connectedAs")}</dt>
              <dd className="text-ink-2" data-testid="linkedin-connected-as">
                {session.connected_as || "—"}
              </dd>
              <dt>{t("settingsPage.session.expires")}</dt>
              <dd>{fmtDate(session.li_at_expires_at)}</dd>
              <dt>{t("settingsPage.session.lastValidated")}</dt>
              <dd>{fmtDate(session.last_validated_at)}</dd>
            </dl>

            {status === "backing_off" && (
              <div
                className="mt-3 rounded-md border border-bad-2 bg-bad-wash p-2.5 text-[11.5px] text-bad"
                data-testid="linkedin-backoff-notice"
              >
                {session.paused_reason
                  ? t("settingsPage.session.backoffNoticeReason", {
                      reason: session.paused_reason,
                    })
                  : t("settingsPage.session.backoffNotice")}
              </div>
            )}

            <div className="mt-3 flex flex-wrap gap-2">
              {!connected && !connecting && (
                <button
                  data-testid="linkedin-connect-btn"
                  onClick={() => connect.mutate()}
                  className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
                >
                  {t("settingsPage.session.connect")}
                </button>
              )}
              {connecting && (
                <span
                  data-testid="linkedin-connecting-hint"
                  className={"inline-flex h-[30px] items-center rounded-md px-3 text-[12px] font-medium " + MUTED_WARN_BOX}
                >
                  {t("settingsPage.session.connectingHint")}
                </span>
              )}
              {connected && (
                <button
                  data-testid="linkedin-validate-btn"
                  onClick={() => validate.mutate()}
                  disabled={validate.isPending}
                  className="inline-flex h-[30px] items-center rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink hover:bg-surface-3 disabled:opacity-60"
                >
                  {validate.isPending
                    ? t("settingsPage.session.validating")
                    : t("settingsPage.session.validate")}
                </button>
              )}
              {validate.isSuccess && !validate.isPending ? (
                <span
                  data-testid="linkedin-validate-result"
                  className="inline-flex h-[30px] items-center text-[12px] text-good"
                >
                  {t("settingsPage.session.validateOk")}
                </span>
              ) : null}
              {validate.isError ? (
                <span
                  data-testid="linkedin-validate-error"
                  className="inline-flex h-[30px] items-center text-[12px] text-bad"
                >
                  {t("settingsPage.session.validateFailed", {
                    message:
                      validate.error instanceof Error
                        ? validate.error.message
                        : t("settingsPage.session.errorFallback"),
                  })}
                </span>
              ) : null}
              {status === "backing_off" && (
                <button
                  data-testid="linkedin-resume-btn"
                  onClick={() => resume.mutate()}
                  className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
                >
                  {t("settingsPage.session.resume")}
                </button>
              )}
              {(connected || status === "expired" || status === "backing_off") && (
                <button
                  data-testid="linkedin-disconnect-btn"
                  onClick={() => disconnect.mutate()}
                  className="inline-flex h-[30px] items-center rounded-md border border-transparent px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
                >
                  {t("settingsPage.session.disconnect")}
                </button>
              )}
            </div>
          </div>

          {/* Account tier (US-REF-08 / US-NW-10) */}
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <div className="flex items-center text-[13px] font-medium text-ink">
                {t("settingsPage.session.tier")}
                <InfoDot label={t("settingsPage.session.tierCapsLabel")}>
                  {t("settingsPage.session.tierInfo")}
                </InfoDot>
              </div>
              <div className="text-[12px] text-ink-3">{t("settingsPage.session.tierHint")}</div>
            </div>
            <select
              data-testid="linkedin-tier-select"
              value={session.account_tier}
              onChange={(e) => setTier.mutate(e.target.value as "new" | "seasoned")}
              className="rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink"
            >
              <option value="new">{t("settingsPage.session.tierNew")}</option>
              <option value="seasoned">{t("settingsPage.session.tierSeasoned")}</option>
            </select>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// Server clamps to this range; keep the UI honest to it.
const LI_LIMIT_MIN = 25;
const LI_LIMIT_MAX = 250;

// The experimental gate around LinkedIn job search — mirrors Referral Outreach
// (hazard badge + ToS risk line + ack + Enable toggle), with its OWN opt-in but
// the SAME shared LinkedIn session (connect once, stays until Disconnect).
function LinkedInJobSearchSection({
  settings,
  patch,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  const { data: session } = useLinkedInSession();
  const [ack, setAck] = useState(false);
  const enabled = settings.linkedin_search_enabled;
  const connected = session?.status === "valid";
  return (
    <Section title={t("settingsPage.linkedinSearch.title")} titleExtra={<ExperimentalHazard />}>
      <div className="space-y-3">
        <p className="text-[12.5px] text-ink-2">
          {t("settingsPage.linkedinSearch.intro")}
          <InfoDot label={t("settingsPage.linkedinSearch.howLabel")}>
            {t("settingsPage.linkedinSearch.howInfo")}
          </InfoDot>
        </p>
        <LinkedInRiskLine detail={t(JOB_SEARCH_WARNING)} />
        <label className="flex items-start gap-2 text-[12px] font-medium text-ink-2">
          <input
            type="checkbox"
            checked={ack || enabled}
            onChange={(e) => setAck(e.target.checked)}
            data-testid="linkedin-search-ack"
            className="mt-0.5"
          />
          {t("settingsPage.linkedinSearch.ack")}
        </label>
        <div className="flex items-center gap-3">
          <div className="flex-1 text-[13px] font-medium text-ink">
            {t("settingsPage.linkedinSearch.enable")}
          </div>
          <Toggle
            on={enabled}
            onChange={(v) => {
              if (v && !ack) return;
              patch(
                v
                  ? { linkedin_search_enabled: v, linkedin_search_ack_at: new Date().toISOString() }
                  : { linkedin_search_enabled: v },
              );
              if (!v) setAck(false);
            }}
            testid="linkedin-search-toggle"
          />
        </div>
        {settings.linkedin_search_ack_at ? (
          <div className="text-[11px] text-ink-4" data-testid="linkedin-search-ack-at">
            {t("settingsPage.acknowledgedOn", {
              date: new Date(settings.linkedin_search_ack_at).toLocaleDateString(),
            })}
          </div>
        ) : null}
        {enabled ? (
          <div className="space-y-3">
            {/* Same collapsible session as Referral Outreach — connect/disconnect
                here or there, it's one shared session. */}
            <LinkedInSessionSection />
            {connected ? (
              <LinkedInJobSearchBlock />
            ) : (
              <p className="text-[11.5px] text-ink-4">
                {t("settingsPage.linkedinSearch.connectHint")}
              </p>
            )}
          </div>
        ) : null}
      </div>
    </Section>
  );
}

function LinkedInJobSearchBlock() {
  const { t } = useTranslation();
  const search = useLinkedinSearch();
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const limit = settings?.linkedin_search_limit ?? 50;
  const onChangeLimit = (v: number) => update.mutate({ linkedin_search_limit: v });
  return (
    <div className="space-y-3" data-testid="linkedin-jobsearch-block">
      <div className="flex items-center gap-3">
        <div className="flex-1 text-[12.5px] text-ink-2">
          {t("settingsPage.linkedinSearch.runNow")}
        </div>
        <button
          data-testid="linkedin-jobsearch-btn"
          onClick={() => search.mutate(limit)}
          disabled={search.isPending}
          className="inline-flex h-[30px] shrink-0 items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:opacity-60"
        >
          {search.isPending
            ? t("settingsPage.linkedinSearch.searching")
            : t("settingsPage.linkedinSearch.searchBtn")}
        </button>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex-1 text-[12.5px] text-ink-2">
          {t("settingsPage.linkedinSearch.resultsPerSearch")}
          <InfoDot label={t("settingsPage.linkedinSearch.resultsPerSearch")}>
            <Trans
              i18nKey="settingsPage.linkedinSearch.resultsPerSearchInfo"
              components={{ strong: <strong /> }}
            />
          </InfoDot>
        </div>
        <select
          value={String(limit)}
          data-testid="linkedin-jobsearch-limit"
          onChange={(e) => onChangeLimit(Number(e.target.value))}
          className="h-[30px] rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink"
        >
          {Array.from(
            { length: (LI_LIMIT_MAX - LI_LIMIT_MIN) / 25 + 1 },
            (_, i) => LI_LIMIT_MIN + i * 25,
          ).map((n) => (
            <option key={n} value={n}>
              {t("settingsPage.linkedinSearch.jobsOption", { n })}
            </option>
          ))}
        </select>
      </div>
      {search.isSuccess ? (
        <div className="text-[11.5px] text-good" data-testid="linkedin-jobsearch-started">
          {t("settingsPage.linkedinSearch.started")}
        </div>
      ) : null}
      {search.isError ? (
        <div className="text-[11.5px] text-bad" data-testid="linkedin-jobsearch-error">
          {search.error instanceof Error
            ? search.error.message
            : t("settingsPage.linkedinSearch.failed")}
        </div>
      ) : null}
    </div>
  );
}
