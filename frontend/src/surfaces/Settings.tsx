// Settings (US-SET / §13) — Automation-on-Save, LLM providers + per-operation
// engine routing, the LinkedIn networking risk toggle w/ warning copy + ack,
// observability, appearance (theme). Ports settings*.html (product sections
// only — the prototype's purple "internal UI testing" mockups are not product).

import { useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api/index";
import { qk } from "../api/queries";
import {
  useConnectLinkedIn,
  useDeleteDiscoveryCredential,
  useDeleteEngine,
  useDisconnectLinkedIn,
  useDiscoveryCredentials,
  useDiscoverySources,
  useLinkedinSearch,
  useLinkedInSession,
  useProfile,
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
  ApplicationProfile,
  EngineVerifyResult,
  LinkedInSessionState,
  OperationKind,
  PromptSetting,
  Settings as SettingsT,
} from "../api/types";
import { InfoDot } from "../shell/InfoDot";
import { type ThemeMode, useThemeMode } from "../shell/theme";

const NETWORKING_WARNING =
  "Automation on LinkedIn of any kind violates LinkedIn's terms of service. finds-you-jobs does not misuse the automation to farm data, sell it, or profit from it, and it keeps the automation 1-to-1 identical to what a human would do — sending messages at human typing speed, respecting daily caps, and randomising timing. But LinkedIn's Terms of Service is violated whatever way we slice it, so we insist you use your own judgement and take full responsibility for the consequences from LinkedIn. Your account may face restrictions, and finds-you-jobs is not responsible for any consequences to your LinkedIn account. Please use this feature responsibly, monitor your sent messages, and turn it off if you notice unusual account behaviour. Not using this feature does not impact your LinkedIn account or any other account in any way.";

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
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

function ThemeModeToggle({
  mode,
  onChange,
}: {
  mode: ThemeMode;
  onChange: (m: ThemeMode) => void;
}) {
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
          {m.label}
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
  { value: 0, label: "Uncapped" },
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
          {p.label}
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
    heading: "Company boards (ATS)",
    blurb: "Direct company careers boards from your source registry.",
  },
  {
    kind: "board",
    heading: "Job boards",
    blurb: "Public keyless boards, scanned whole and filtered locally.",
  },
  {
    kind: "search",
    heading: "Search sources",
    blurb: "Queried with your role aliases × locations each scan.",
  },
  {
    kind: "apify",
    heading: "Apify",
    blurb: "Actor-run boards on your own Apify key (Naukri, Indeed, Seek…).",
  },
  {
    kind: "fallback",
    heading: "Feeds",
    blurb: "Any RSS/Atom feed URL you add as a source.",
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
            Remove
          </button>
        </>
      ) : (
        <>
          <input
            type="password"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="API key"
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
            Save
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
  return (
    <input
      type="checkbox"
      checked={checked}
      ref={(el) => {
        if (el) el.indeterminate = indeterminate;
      }}
      onChange={(e) => onChange(e.target.checked)}
      data-testid={testid}
      title="Enable or disable every source in this section"
    />
  );
}

function DiscoverySourcesSection() {
  const { data: sources } = useDiscoverySources();
  const toggle = useToggleDiscoverySource();
  if (!sources) return null;
  return (
    <div className="space-y-4" data-testid="discovery-sources">
      <p className="text-[12px] text-ink-3">
        Every source is on by default. Untick one to skip it on every future scan — for
        example, if an ATS never carries roles for your field or location. The checkbox
        on a section title flips the whole section at once. Nothing else changes:
        already-found jobs stay, and re-ticking picks the source back up on the next
        scan.
      </p>
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
              <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-ink-4">
                {heading}
              </span>
            </label>
            <div className="text-[11.5px] text-ink-4">{blurb}</div>
            {isApify && rows.length === 0 ? (
              <div className="text-[11.5px] text-ink-4">
                Save your Apify key below to add its actor sources.
              </div>
            ) : null}
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 pt-1">
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
                  <span className={s.enabled ? "" : "text-ink-4 line-through"}>{s.label}</span>
                  {s.entries > 0 ? (
                    <span className="text-[11px] text-ink-4">
                      {s.entries} board{s.entries === 1 ? "" : "s"}
                    </span>
                  ) : null}
                </label>
              ))}
            </div>
          </div>
        );
      })}
      <div className="space-y-3 border-t border-border pt-4">
        <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-ink-4">
          Bring-your-own-key sources
        </div>
        <p className="text-[11.5px] text-ink-4">
          Optional. These cover boards we can&apos;t scrape cleanly first-party (Indeed,
          Naukri, Seek). Keys are encrypted at rest and only ever sent to that provider.
        </p>
        <CredentialRow
          id="apify"
          label="Apify"
          hint="Runs job-scraper actors on your Apify account — a free account (~$5/mo credit, no card) covers roughly 5,000 jobs/month."
        />
        <CredentialRow
          id="brave"
          label="Brave Search"
          hint="Finds fresh postings on ATS boards outside your registry via Brave's Search API — free tier is ~2,000 queries/month (we stop at the cap)."
        />
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="font-mono text-[10.5px] font-medium uppercase tracking-[0.12em] text-ink-3">
        {title}
      </h2>
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
  const lc = settings.lifecycle;
  // Merge-patch a single field so unrelated windows aren't clobbered (mirrors the
  // observability patch shape — the mock/real clients replace the whole object).
  const set = (k: keyof SettingsT["lifecycle"]) => (v: number) =>
    patch({ lifecycle: { ...lc, [k]: v } });
  return (
    <Section title="Contact & data lifecycle">
      <div className="space-y-4">
        <p className="text-[12px] text-ink-3">
          How long finds-you-jobs keeps and auto-advances your data. Networking contacts move
          themselves along the kanban and quiet threads mark <em>Ghosted</em>; deleted items
          are purged for good after their window. <strong>Converted contacts are never
          auto-changed.</strong>
        </p>
        <LifecycleRow
          label="Engagement → Ghosted"
          hint="A replied-to thread with no new activity for this long is marked Ghosted."
          unit="days"
          value={lc.engagement_ghosted_days}
          onChange={set("engagement_ghosted_days")}
          testid="engagement-ghosted"
        />
        <LifecycleRow
          label="Sent / Accepted → Ghosted"
          hint="A connection that's never accepted, or accepted but never replied to, ghosts after this."
          unit="days"
          value={lc.sent_ghosted_days}
          onChange={set("sent_ghosted_days")}
          testid="sent-ghosted"
        />
        <LifecycleRow
          label="Purge deleted contacts"
          hint="Archived (deleted) contacts are permanently removed this long after you delete them."
          unit="days"
          value={lc.contact_purge_days}
          onChange={set("contact_purge_days")}
          testid="contact-purge"
        />
        <LifecycleRow
          label="Purge trashed jobs"
          hint="Jobs left in Trash are permanently removed (and suppressed from re-scraping) after this."
          unit="days"
          value={lc.trashed_jobs_purge_days}
          onChange={set("trashed_jobs_purge_days")}
          testid="trashed-jobs-purge"
        />
        <LifecycleRow
          label="Purge archived applications"
          hint="Archived tracker cards (and their tailored docs) are permanently removed after this."
          unit="days"
          value={lc.archived_applications_purge_days}
          onChange={set("archived_applications_purge_days")}
          testid="archived-apps-purge"
        />
        {settings.networking_enabled ? (
          <LifecycleRow
            label="Contact status sync cadence"
            hint="How often finds-you-jobs checks LinkedIn to advance your contacts (only while Referral Outreach is on + connected)."
            unit="hours"
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
  { id: "claude-cli", label: "Claude subscription (CLI)" },
  { id: "codex-cli", label: "ChatGPT subscription (Codex CLI)" },
  { id: "antigravity-cli", label: "Google subscription (Antigravity CLI)" },
];
const isCliEngine = (id: string) => CLI_ENGINE_OPTIONS.some((o) => o.id === id);

// ─── Engine routing + editable prompts (FR-SET-11) ──────────────────────────
// Each LLM operation is a collapsible row: header shows the engine/model
// summary + an "edited" badge; expanded reveals the engine selector (routed
// kinds only) and a monospace editor for that operation's system prompt (the
// module skill markdown), with Save/Reset. Collapsed by default so the large
// prompt text never overwhelms the page.

export function PromptRoutingRow({
  prompt,
  settings,
  patch,
}: {
  prompt: PromptSetting;
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [modelDraft, setModelDraft] = useState<string | null>(null);
  const setPrompt = useSetPrompt();
  const resetPrompt = useResetPrompt();

  const edited = prompt.override_md != null;
  const baseText = prompt.override_md ?? prompt.default_md;
  const text = draft ?? baseText;
  const dirty = text !== baseText;

  // Engine selector (routed kinds only) — same behavior/markup as before: the
  // select picks the ENGINE; changing it clears the per-kind model so the
  // engine's own default applies.
  const route = settings.routing.find((r) => r.kind === prompt.kind);
  const engine = route?.engine || "claude-cli";
  const effectiveModel =
    route?.model ||
    (engine === "claude-cli"
      ? CLAUDE_CLI_DEFAULT_MODEL
      : isCliEngine(engine)
        ? "CLI default model"
        : settings.providers.find((p) => p.id === engine)?.default_model) ||
    "provider default";
  const engineLabel =
    CLI_ENGINE_OPTIONS.find((o) => o.id === engine)?.label ||
    settings.providers.find((p) => p.id === engine)?.label ||
    engine;
  const options = [
    ...CLI_ENGINE_OPTIONS,
    ...settings.providers.filter((p) => p.configured).map((p) => ({ id: p.id, label: p.label })),
  ];

  function save() {
    setPrompt.mutate(
      { kind: prompt.kind, markdown: text },
      { onSuccess: () => setDraft(null) },
    );
  }
  function reset() {
    if (!window.confirm("Reset this prompt to the shipped default? Your edits will be lost."))
      return;
    resetPrompt.mutate(prompt.kind, { onSuccess: () => setDraft(null) });
  }

  return (
    <div className="rounded-md border border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid={`prompt-row-${prompt.kind}`}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="w-3 text-ink-4">{open ? "▾" : "▸"}</span>
        <span className="w-16 font-mono text-[11px] uppercase text-ink-3">{prompt.kind}</span>
        <span className="text-[12.5px] font-medium text-ink">{prompt.title}</span>
        {edited ? (
          <span
            data-testid={`prompt-edited-${prompt.kind}`}
            className="rounded-full bg-accent-wash px-1.5 py-0.5 text-[10px] font-medium text-accent"
          >
            edited
          </span>
        ) : null}
        <span className="ml-auto truncate text-[11px] text-ink-4">
          {prompt.routed ? `${engineLabel} · ${effectiveModel}` : "prompt-only"}
        </span>
      </button>
      {open ? (
        <div className="space-y-2 border-t border-border px-3 py-3">
          {prompt.routed ? (
            <div className="flex items-center gap-3">
              <span className="text-[11.5px] text-ink-3">Engine</span>
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
                className="flex-1 rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink"
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
                title={`Model this operation uses on ${engineLabel}. Blank = ${effectiveModel} (the provider/CLI default).`}
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
                className="w-44 truncate rounded-md border border-border bg-surface px-2 py-1 text-right text-[11px] text-ink-2"
              />
            </div>
          ) : null}
          <textarea
            value={text}
            spellCheck={false}
            data-testid={`prompt-textarea-${prompt.kind}`}
            onChange={(e) => setDraft(e.target.value)}
            className="h-56 w-full resize-y rounded-md border border-border bg-surface-2 px-2 py-1.5 font-mono text-[11.5px] leading-relaxed text-ink"
          />
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-ink-4" data-testid={`prompt-chars-${prompt.kind}`}>
              {text.length} chars{edited ? " · override active" : " · shipped default"}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={reset}
                disabled={!edited}
                data-testid={`prompt-reset-${prompt.kind}`}
                className="rounded-md border border-border px-2.5 py-1 text-[12px] text-ink-2 disabled:opacity-40"
              >
                Reset
              </button>
              <button
                type="button"
                onClick={save}
                disabled={!dirty || !text.trim()}
                data-testid={`prompt-save-${prompt.kind}`}
                className="rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-white disabled:opacity-40"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      ) : null}
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
  const { data: prompts } = usePrompts();
  return (
    <div className="border-border pt-1">
      <div className="mb-2 text-[12px] font-medium text-ink-2">
        Per-operation routing + prompts (strong model for what a human reads, cheap for machine
        filters). Expand a row to edit its engine and system prompt.
      </div>
      <div className="space-y-2">
        {(prompts ?? []).map((prompt) => (
          <PromptRoutingRow key={prompt.kind} prompt={prompt} settings={settings} patch={patch} />
        ))}
      </div>
    </div>
  );
}

// ─── Application profile editor (FR-APP-01, 2026-07-11) ─────────────────────

const AP_FIELDS: { key: keyof ApplicationProfile & string; label: string }[] = [
  { key: "name", label: "Full name" },
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "location", label: "Location (city)" },
  { key: "country", label: "Country" },
  { key: "work_authorization", label: "Work authorization" },
];

function ApplicationProfileEditor() {
  const qc = useQueryClient();
  const profile = useProfile();
  const stored = profile.data?.application_profile ?? null;
  const [draft, setDraft] = useState<ApplicationProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const record = draft ?? stored;

  async function save(): Promise<void> {
    if (draft == null) return;
    setBusy(true);
    try {
      await Promise.resolve(api.patchApplicationProfile(draft));
      setDraft(null);
      await qc.invalidateQueries({ queryKey: qk.profile });
    } finally {
      setBusy(false);
    }
  }

  async function reExtract(): Promise<void> {
    setBusy(true);
    try {
      await Promise.resolve(api.extractApplicationProfile());
      await qc.invalidateQueries({ queryKey: qk.profile });
    } finally {
      setBusy(false);
    }
  }

  if (!profile.data?.master_md) {
    return (
      <p className="text-[12px] text-ink-3" data-testid="ap-editor-empty">
        Save a master resume first — the profile is extracted from it.
      </p>
    );
  }
  if (record == null) {
    return (
      <div className="flex items-center gap-3" data-testid="ap-editor-none">
        <p className="flex-1 text-[12px] text-ink-3">
          No application profile yet — extract one from your master resume.
        </p>
        <button
          onClick={reExtract}
          disabled={busy}
          className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12px] text-white disabled:opacity-50"
          data-testid="ap-extract-now"
        >
          {busy ? "Extracting…" : "Extract now"}
        </button>
      </div>
    );
  }

  const set = (key: string, value: string) =>
    setDraft({ ...(record as ApplicationProfile), [key]: value });
  const education = record.education ?? [];
  const setEdu = (i: number, key: string, value: string) => {
    const rows = education.map((e, j) => (j === i ? { ...e, [key]: value } : e));
    setDraft({ ...(record as ApplicationProfile), education: rows });
  };

  return (
    <div className="space-y-3" data-testid="ap-editor">
      <div className="grid grid-cols-2 gap-2">
        {AP_FIELDS.map(({ key, label }) => (
          <label key={key} className="text-[11.5px] text-ink-3">
            {label}
            <input
              value={String(record[key] ?? "")}
              onChange={(e) => set(key, e.target.value)}
              data-testid={`ap-field-${key}`}
              className="mt-0.5 w-full rounded-md border border-border-2 bg-surface-2 px-2 py-1.5 text-[12.5px] text-ink"
            />
          </label>
        ))}
      </div>
      {education.length > 0 ? (
        <div className="space-y-1.5">
          <div className="text-[11.5px] font-medium text-ink-2">Education</div>
          {education.map((e, i) => (
            <div key={i} className="grid grid-cols-5 gap-1.5">
              {(["school", "degree", "discipline", "start_year", "end_year"] as const).map(
                (k) => (
                  <input
                    key={k}
                    value={String(e[k] ?? "")}
                    onChange={(ev) => setEdu(i, k, ev.target.value)}
                    placeholder={k.replace("_", " ")}
                    className="rounded-md border border-border-2 bg-surface-2 px-2 py-1.5 text-[12px] text-ink"
                  />
                ),
              )}
            </div>
          ))}
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-ink-3">
          {record.source === "edited" ? "Manually edited — your values win." : "Extracted from your resume."}
        </span>
        <div className="flex gap-2">
          <button
            onClick={reExtract}
            disabled={busy}
            className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12px] text-ink-2 hover:bg-surface-3 disabled:opacity-50"
            data-testid="ap-reextract"
          >
            Re-extract
          </button>
          <button
            onClick={save}
            disabled={busy || draft == null}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12px] text-white disabled:opacity-50"
            data-testid="ap-save"
          >
            Save profile
          </button>
        </div>
      </div>
    </div>
  );
}

// AI Providers panel (FR-SET-06 / US-SET-07). The tile catalog is a static P1
// menu; configured/verified state is cross-referenced from settings.providers
// (the persisted EngineSettings rows) and "In use" from the routing map.
type ProviderCatalogEntry = {
  id: string;
  label: string;
  badge?: string;
  kind: "key" | "local";
  desc: string;
  modelChips?: string[];
  modelPlaceholder?: string;
};

const PROVIDER_CATALOG: ProviderCatalogEntry[] = [
  {
    id: "openrouter",
    label: "OpenRouter",
    badge: "Recommended",
    kind: "key",
    desc: "One key, most models — the simplest bring-your-own-key path.",
    modelPlaceholder: "e.g. anthropic/claude-opus-4-8",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    kind: "key",
    desc: "Direct Anthropic API key (x-api-key).",
    modelChips: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
  },
  {
    id: "openai",
    label: "OpenAI",
    kind: "key",
    desc: "Direct OpenAI API key (Bearer).",
    modelChips: ["gpt-5", "gpt-5-mini", "gpt-4o", "gpt-4o-mini"],
  },
  {
    id: "local",
    label: "Local LLM",
    kind: "local",
    desc: "Point at a running Ollama / LM Studio / vLLM server — nothing leaves your machine.",
    modelPlaceholder: "e.g. llama3.1:70b",
  },
];

const INPUT_CLS =
  "w-full rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink placeholder:text-ink-4";

// Subscription-CLI rows in the AI Providers panel (verify-only — no key, no
// persisted row; routing under "Engine routing & prompts" selects them).
const SUBSCRIPTION_CLIS: { id: string; label: string; desc: string; experimental?: boolean }[] = [
  { id: "claude-cli", label: "Claude subscription (CLI)", desc: "Your logged-in Claude Code CLI." },
  {
    id: "codex-cli",
    label: "ChatGPT subscription (Codex CLI)",
    desc: "Your logged-in OpenAI Codex CLI.",
  },
  {
    id: "antigravity-cli",
    label: "Google subscription (Antigravity CLI)",
    desc: "Uses your agy login. Verify runs a real test prompt — agy's non-interactive mode has known rough edges upstream.",
    experimental: true,
  },
];

function AIProvidersPanel({ settings }: { settings: SettingsT }) {
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
  const noVerified = !settings.providers.some((p) => p.configured);

  return (
    <div className="space-y-4" data-testid="ai-providers-panel">
      {noVerified && (
        <div
          data-testid="no-provider-warning"
          className="rounded-lg border border-warn-2 bg-warn-wash p-3 text-[11.5px] text-warn"
        >
          No provider is configured yet — scoring and tailoring stay disabled until you verify and
          save one below.
        </div>
      )}

      {/* Tile grid */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {PROVIDER_CATALOG.map((e) => {
          const row = settings.providers.find((p) => p.id === e.id);
          const active = e.id === selected;
          return (
            <button
              key={e.id}
              data-testid={`provider-tile-${e.id}`}
              onClick={() => select(e.id)}
              className={
                "rounded-lg border p-3 text-left transition-colors " +
                (active
                  ? "border-accent ring-1 ring-accent bg-surface"
                  : "border-border bg-surface hover:bg-surface-3")
              }
            >
              <div className="flex items-center gap-1.5">
                <span className="text-[13px] font-medium text-ink">{e.label}</span>
                {e.badge && (
                  <span className="rounded-full bg-good-wash px-1.5 py-0.5 text-[9px] font-medium uppercase text-good">
                    {e.badge}
                  </span>
                )}
              </div>
              <div className="mt-1 font-mono text-[9.5px] uppercase tracking-wider text-ink-3">
                {row?.configured ? "configured" : "not set"}
              </div>
            </button>
          );
        })}
        <div
          data-testid="provider-tile-embedded"
          className="rounded-lg border border-dashed border-border p-3 text-left opacity-50"
        >
          <div className="text-[13px] font-medium text-ink-3">Embedded local LLM</div>
          <div className="mt-1 font-mono text-[9.5px] uppercase tracking-wider text-ink-4">
            coming soon
          </div>
        </div>
      </div>

      {/* Subscription CLIs — verify-only providers (no key, nothing persisted);
          route operations to one under "Engine routing & prompts" below. */}
      <div
        className="rounded-lg border border-border bg-surface-2 p-3"
        data-testid="cli-providers-panel"
      >
        <div className="text-[13px] font-medium text-ink">Subscription CLIs</div>
        <p className="mt-1 text-[12px] text-ink-3">
          Use a coding CLI you're already logged into — no API key, your subscription pays. Verify
          checks the login; pick one per operation under Engine routing &amp; prompts.
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
                    <span className="text-[12.5px] font-medium text-ink">{c.label}</span>
                    {c.experimental ? (
                      <span className="rounded-full bg-warn-wash px-1.5 py-0.5 text-[9px] font-medium uppercase text-warn">
                        Experimental
                      </span>
                    ) : null}
                    {settings.routing.some((r) => r.engine === c.id) ? (
                      <span className="rounded-full bg-good-wash px-1.5 py-0.5 text-[9px] font-medium uppercase text-good">
                        In use
                      </span>
                    ) : null}
                  </span>
                  <span className="block truncate text-[11px] text-ink-3">{c.desc}</span>
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
                  {busy ? "Verifying…" : res?.ok ? "Verified ✓" : "Verify"}
                </button>
              </div>
            );
          })}
        </div>
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
              In use
            </span>
          )}
        </div>
        <p className="mt-1 text-[12px] text-ink-3">{entry.desc}</p>

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
                  ? `Key saved (${savedRow.key_hint ?? "•••"}) — paste to replace`
                  : "Paste your API key"
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
            {verify.isPending ? "Verifying…" : "Verify"}
          </button>
          <button
            data-testid="engine-save-btn"
            onClick={() => save.mutate(input, { onSuccess: () => setKey("") })}
            className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
          >
            Save
          </button>
          {savedRow && (
            <button
              data-testid="engine-delete-btn"
              onClick={() => del.mutate(selected)}
              className="inline-flex h-[30px] items-center rounded-md border border-transparent px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
            >
              Remove
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
            {result.ok ? "✓ Verified" : result.detail}
          </div>
        )}
      </div>
    </div>
  );
}

export function Settings() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const [themeMode, , setThemeMode] = useThemeMode();
  const [ack, setAck] = useState(false);

  if (!settings) return null;

  function patch(p: Partial<SettingsT>) {
    update.mutate(p);
  }

  return (
    <>
      <header className="flex min-h-[48px] items-center border-b border-border bg-surface px-5" />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-8 p-6">
          <header className="space-y-2">
            <h1 className="text-[20px] font-semibold text-ink">Settings</h1>
            <p className="text-[13px] text-ink-3">
              Configure how finds-you-jobs scrapes, scores, and reaches out on your behalf.
            </p>
          </header>

          {/* Discovery sources — per-family opt-out toggles (2026-07-18).
              First section on purpose: discovery is the first stage of the
              pipeline everything below feeds on. */}
          <Section title="Discovery sources">
            <DiscoverySourcesSection />
          </Section>

          {/* Automation on Save — split defaults (FR-SET-02): Resume ON, Cover ON */}
          <Section title="Automation on Save">
            <div className="space-y-4">
              <p className="text-[12px] text-ink-3">
                These are your <strong>defaults for every job you save</strong>. Need something
                different for one job? Flip its per-job toggles on the Job Board before you save —
                that doesn&apos;t change the defaults here.
              </p>
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="text-[13px] font-medium text-ink">
                    Tailor my resume when I save a job
                  </div>
                  <div className="text-[12px] text-ink-3">
                    On Save, tailor your resume in the background so it&apos;s ready on the tracker
                    card. Review it before you copy or export — nothing is auto-submitted.
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
                    Draft a cover letter when I save a job
                  </div>
                  <div className="text-[12px] text-ink-3">
                    On Save, draft a cover letter from your master profile and the job description in
                    the background — a separate step from the resume.
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
                    <div className="text-[13px] font-medium text-ink">
                      Find referrals when I save a job
                    </div>
                    <div className="text-[12px] text-ink-3">
                      On Save, start Referral Outreach for the job in the background. You still
                      confirm the company and approve every message before anything is sent.
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

          {/* Scoring MODE (maintainer design 2026-07-22): every scanned job is
              scored — the old off-switch is retired ("a user can always sort
              by recency and ignore the scoring anyways"); the choice is HOW.
              AI failures fall back to a grey keyword score (retry in Logs). */}
          <Section title="Scoring">
            <div className="space-y-4">
              <div>
                <div className="text-[13px] font-medium text-ink">How jobs are scored</div>
                <div className="mb-2 text-[12px] text-ink-3">
                  Every new job gets a fit score against your master resume. If an AI score
                  fails, the keyword score fills in (grey) and you can retry from
                  Analytics → Logs.
                </div>
                <div className="flex flex-col gap-1.5" data-testid="scoring-mode-picker">
                  {(
                    [
                      [
                        "llm",
                        "AI scoring — best quality, but costs LLM tokens and some time",
                      ],
                      ["keyword", "Keyword scoring — lower quality, but free and instant"],
                    ] as const
                  ).map(([mode, label]) => (
                    <button
                      key={mode}
                      type="button"
                      data-testid={`scoring-mode-${mode}`}
                      data-on={settings.scoring_mode === mode}
                      onClick={() => patch({ scoring_mode: mode })}
                      className={
                        "rounded-md border px-3 py-2 text-left text-[12.5px] " +
                        (settings.scoring_mode === mode
                          ? "border-accent bg-accent-wash text-accent-ink"
                          : "border-border-2 bg-surface text-ink-2 hover:bg-surface-3")
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {settings.scoring_mode === "llm" ? (
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <div className="text-[13px] font-medium text-ink">Scoring batch cap</div>
                    <div className="text-[12px] text-ink-3">
                      Limit how many newly-scanned jobs get scored in one scheduler pass. Uncapped
                      scores everything found; a cap spreads the LLM cost across more ticks.
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
                  <div className="text-[13px] font-medium text-ink">Parallel AI calls</div>
                  <div className="text-[12px] text-ink-3">
                    How many scoring / tailoring / drafting calls run at once. Higher =
                    faster board, but the spend arrives just as fast and your provider may
                    rate-limit bursts (429s). Unlimited removes the app's cap entirely —
                    you own that tradeoff.
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
                        {n} at once
                      </option>
                    ))}
                    <option value={0}>Unlimited</option>
                  </select>
                </div>
              </div>
            </div>
          </Section>

          {/* The prior repository's auto-submit toggle (apply_mode) is retired:
              P1 ships no submit capability at all — the agent's tool vocabulary
              has no submit tool, and a card reaches Applied only via detected
              confirmation or your explicit attestation (RELEASING.md, the P2
              boundary). The section stays as an honest statement of that. */}
          <Section title="Applier">
            <div className="flex-1" data-testid="applier-p1-boundary">
              <div className="text-[13px] font-medium text-ink">
                The Applier never submits in P1
              </div>
              <div className="text-[12px] text-ink-3">
                It fills the form to the best of your real facts, then hands you the open
                browser to review and click Submit yourself. Autonomous submission is a
                P2 capability behind an explicit delegation opt-in — it does not exist in
                this build.
              </div>
            </div>
          </Section>

          {/* Application profile (FR-APP-01, 2026-07-11) — the structured
              form-fill facts the Applier uses; extracted at master-save,
              editable here (edits always win over extraction). */}
          <Section title="Application profile">
            <div className="space-y-4">
              <p className="text-[12px] text-ink-3">
                Extracted from your resume automatically whenever you save it (one small AI
                call) — the name / contact / location / education record used to fill
                application forms. Your edits below always win.
              </p>
              <ApplicationProfileEditor />
            </div>
          </Section>

          {/* AI Providers (FR-SET-06 / US-SET-07) — tile grid + Verify + Save */}
          <Section title="AI Providers">
            <AIProvidersPanel settings={settings} />
          </Section>

          {/* Per-operation engine routing + editable system prompts (FR-SET-11) */}
          <Section title="Engine routing & prompts">
            <EngineRoutingSection settings={settings} patch={patch} />
          </Section>

          {/* Referral Outreach risk toggle — the canonical feature name for the
              automated LinkedIn module (maintainer, 2026-07-10). The Networking
              tab (contact CRM + kanban + manual tracking) is ALWAYS available and
              carries no risk; this gates only the automated actions. This section
              is deliberately the feature's ONE reveal point (it is never
              advertised elsewhere), so the copy carries the full context. */}
          <Section title="Referral Outreach (experimental)">
            <div className="space-y-3">
              <p className="text-[12.5px] text-ink-2">
                Tracking contacts by hand (the Networking tab) is always on and safe.{" "}
                <strong>Referral Outreach</strong> is the automated part: for a job you pick, it
                finds people at that company and — after you confirm each batch — messages them from
                your own LinkedIn account to ask for a referral. Off by default; you can also use it
                drafts-only and send the messages yourself.
                <InfoDot label="How Referral Outreach works">
                  It finds <em>current</em> employees at the company and drafts a short message for
                  each from a fixed per-role template (peer / hiring-manager / recruiter /
                  leadership) that you can edit — or hit Regenerate for an AI version grounded in
                  your profile. Sending goes through your own LinkedIn session as connection
                  requests or DMs, paced slowly with conservative daily/weekly caps to reduce
                  detection risk.
                </InfoDot>
              </p>
              <div className="rounded-lg border border-warn-2 bg-warn-wash p-3 text-[11.5px] leading-relaxed text-warn">
                {NETWORKING_WARNING}
              </div>
              <label className="flex items-start gap-2 text-[12px] font-medium text-ink-2">
                <input
                  type="checkbox"
                  checked={ack || settings.networking_enabled}
                  onChange={(e) => setAck(e.target.checked)}
                  data-testid="networking-ack"
                  className="mt-0.5"
                />
                I want to automate LinkedIn outreach seeking referrals, at the cost of BREAKING
                LinkedIn&apos;s Terms of Service — which can lead to account restrictions, up to a
                permanent ban. I accept full responsibility.
              </label>
              <div className="flex items-center gap-3">
                <div className="flex-1 text-[13px] font-medium text-ink">Enable Referral Outreach</div>
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
                  Acknowledged on {new Date(settings.networking_ack_at).toLocaleDateString()}
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
                  Turn the toggle on to unlock the next step: connecting your LinkedIn
                  session (required for auto-discovery and sending).
                </div>
              )}
            </div>
          </Section>

          {/* Observability */}
          <Section title="Observability">
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="text-[13px] font-medium text-ink">Content logging</div>
                  <div className="text-[12px] text-ink-3">
                    Log prompt/output content locally for self-debugging (off by default; sizes +
                    fingerprints only otherwise).
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
                  <div className="text-[13px] font-medium text-ink">OTLP export</div>
                  <div className="text-[12px] text-ink-3">
                    Off by default — spans stay in the local store and nothing leaves your machine.
                    Turn on to also send spans to an external OTLP endpoint (Honeycomb, Grafana,
                    Logfire Cloud, …).
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
                  <div className="flex-1 text-[13px] text-ink-2">OTLP endpoint</div>
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
                  <div className="flex-1 text-[13px] text-ink-2">OTLP headers</div>
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
                  <div className="text-[13px] text-ink-2">Local log retention (days)</div>
                  <div className="text-[12px] text-ink-3">
                    Spans older than this are pruned from the local store.
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

          {/* Appearance */}
          <Section title="Appearance">
            <div className="flex items-center gap-3">
              <div className="flex-1">
                <div className="text-[13px] font-medium text-ink">Theme</div>
                <div className="text-[11.5px] text-ink-3">
                  Follow system matches your OS light/dark setting.
                </div>
              </div>
              <ThemeModeToggle mode={themeMode} onChange={setThemeMode} />
            </div>
          </Section>
        </div>
      </main>
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

function statusPill(status: LinkedInSessionState["status"]): PillVariant {
  switch (status) {
    case "valid":
      return { cls: "bg-good-wash border-good-2 text-good", dot: "#1F9D55", label: "Connected" };
    case "connecting":
      return { cls: "bg-warn-wash border-warn-2 text-warn", dot: "#C5A24A", label: "Connecting…" };
    case "backing_off":
      return { cls: "bg-bad-wash border-bad-2 text-bad", dot: "#B23A3A", label: "Backing off" };
    case "expired":
      return { cls: "bg-bad-wash border-bad-2 text-bad", dot: "#B23A3A", label: "Session expired" };
    default:
      return { cls: "bg-bad-wash border-bad-2 text-bad", dot: "#B23A3A", label: "Disconnected" };
  }
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function LinkedInSessionSection() {
  const { data: session } = useLinkedInSession();
  const connect = useConnectLinkedIn();
  const disconnect = useDisconnectLinkedIn();
  const validate = useValidateLinkedIn();
  const resume = useResumeLinkedIn();
  const setTier = useSetLinkedInTier();

  if (!session) return null;
  const status = session.status;
  const pill = statusPill(status);
  const connecting = status === "connecting" || connect.isPending;
  const connected = status === "valid";

  return (
    <div className="rounded-lg border border-border bg-surface-2 p-4">
      <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-3">
        Next step — connect your LinkedIn session
      </div>
      <div className="space-y-4" data-testid="linkedin-session-section">
        <p className="text-[12.5px] text-ink-3">
          Connect your LinkedIn account so finds-you-jobs can find referrers and reach out for you —
          with your own login, and <strong>your session stays on your device</strong>.
          <InfoDot label="How connecting + your session work">
            Connect opens a real browser window on LinkedIn&apos;s own login page; you log in there
            (2FA included), so finds-you-jobs never sees your password. It then keeps only the session
            cookie — encrypted at rest (Fernet) in the app&apos;s local data folder on your machine,
            never uploaded anywhere. The window stays open so you can watch outreach happen in the
            same session; close it when you&apos;re done. Disconnect deletes the saved session and
            the app&apos;s browser profile from this device — it does <strong>not</strong> log you
            out of LinkedIn itself (do that at linkedin.com if you want the session revoked
            server-side).
          </InfoDot>
        </p>

        {/* Status row */}
        <div className="rounded-lg border border-border bg-surface-2 p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="text-[12px] font-medium text-ink-2">Current status</div>
            <span
              data-testid="linkedin-status-pill"
              className={
                "inline-flex h-[22px] items-center gap-[5px] rounded-full border px-2 text-[11.5px] font-medium " +
                pill.cls
              }
            >
              <span className="h-2 w-2 rounded-full" style={{ background: pill.dot }} />
              {pill.label}
            </span>
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[12px] text-ink-3">
            <dt>Connected as</dt>
            <dd className="text-ink-2" data-testid="linkedin-connected-as">
              {session.connected_as || "—"}
            </dd>
            <dt>Session expires</dt>
            <dd>{fmtDate(session.li_at_expires_at)}</dd>
            <dt>Last validated</dt>
            <dd>{fmtDate(session.last_validated_at)}</dd>
          </dl>

          {status === "backing_off" && (
            <div
              className="mt-3 rounded-md border border-bad-2 bg-bad-wash p-2.5 text-[11.5px] text-bad"
              data-testid="linkedin-backoff-notice"
            >
              Outreach is paused after a LinkedIn rate-limit signal
              {session.paused_reason ? `: "${session.paused_reason}"` : "."} Fix the underlying
              issue, then Resume to send again.
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            {!connected && !connecting && (
              <button
                data-testid="linkedin-connect-btn"
                onClick={() => connect.mutate()}
                className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
              >
                Connect LinkedIn
              </button>
            )}
            {connecting && (
              <span
                data-testid="linkedin-connecting-hint"
                className="inline-flex h-[30px] items-center rounded-md border border-warn-2 bg-warn-wash px-3 text-[12px] font-medium text-warn"
              >
                A browser window opened — finish logging in there…
              </span>
            )}
            {connected && (
              <button
                data-testid="linkedin-validate-btn"
                onClick={() => validate.mutate()}
                disabled={validate.isPending}
                className="inline-flex h-[30px] items-center rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink hover:bg-surface-3 disabled:opacity-60"
              >
                {validate.isPending ? "Validating…" : "Validate"}
              </button>
            )}
            {/* Validate feedback (2026-07-12 — clicking used to do "nothing"
                visibly): a local check of the saved cookie, never a LinkedIn
                call, so say what happened either way. */}
            {validate.isSuccess && !validate.isPending ? (
              <span
                data-testid="linkedin-validate-result"
                className="inline-flex h-[30px] items-center text-[12px] text-good"
              >
                Session checked ✓ — status + &quot;Last validated&quot; updated (local check, no
                LinkedIn call)
              </span>
            ) : null}
            {validate.isError ? (
              <span
                data-testid="linkedin-validate-error"
                className="inline-flex h-[30px] items-center text-[12px] text-bad"
              >
                Validate failed: {validate.error instanceof Error ? validate.error.message : "error"}
              </span>
            ) : null}
            {status === "backing_off" && (
              <button
                data-testid="linkedin-resume-btn"
                onClick={() => resume.mutate()}
                className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
              >
                Resume outreach
              </button>
            )}
            {(connected || status === "expired" || status === "backing_off") && (
              <button
                data-testid="linkedin-disconnect-btn"
                onClick={() => disconnect.mutate()}
                className="inline-flex h-[30px] items-center rounded-md border border-transparent px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
              >
                Disconnect
              </button>
            )}
          </div>
        </div>

        {/* Account tier (US-REF-08 / US-NW-10) */}
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="text-[13px] font-medium text-ink">Account tier</div>
            <div className="text-[12px] text-ink-3">
              Caps are owned by the LinkedIn worker; pick the tier that matches your account
              honestly. New = 15/day · 100/wk. Seasoned = 30/day · 200/wk.
            </div>
          </div>
          <select
            data-testid="linkedin-tier-select"
            value={session.account_tier}
            onChange={(e) => setTier.mutate(e.target.value as "new" | "seasoned")}
            className="rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink"
          >
            <option value="new">New account (safe default)</option>
            <option value="seasoned">Seasoned account</option>
          </select>
        </div>

        {/* One-shot logged-in job search (discovery-expansion #6). A
            user-clicked entry point ONLY — scheduled scans never touch the
            logged-in session (that's what the guest adapter is for). Uses your
            saved roles × locations; results land in the normal Job Board feed,
            deduped against guest/Apify finds. Read-only against LinkedIn. */}
        {connected ? <LinkedInJobSearchBlock /> : null}
      </div>
    </div>
  );
}

// Server clamps to this range; keep the UI honest to it.
const LI_LIMIT_MIN = 25;
const LI_LIMIT_MAX = 250;

function LinkedInJobSearchBlock() {
  const search = useLinkedinSearch();
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const limit = settings?.linkedin_search_limit ?? 50;
  const onChangeLimit = (v: number) => update.mutate({ linkedin_search_limit: v });
  return (
    <div
      className="space-y-3 border-t border-border pt-4"
      data-testid="linkedin-jobsearch-block"
    >
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <div className="text-[13px] font-medium text-ink">Search LinkedIn jobs now</div>
          <div className="text-[12px] text-ink-3">
            Run a one-off logged-in job search using your saved roles and locations. Results
            appear in your Job Board, deduped against everything else. This uses your session
            only when you click — scheduled scans never do.
          </div>
        </div>
        <button
          data-testid="linkedin-jobsearch-btn"
          onClick={() => search.mutate(limit)}
          disabled={search.isPending}
          className="inline-flex h-[30px] shrink-0 items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:opacity-60"
        >
          {search.isPending ? "Searching…" : "Search LinkedIn jobs"}
        </button>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <div className="text-[12.5px] text-ink-2">Results per search</div>
          <div className="text-[11.5px] text-ink-4">
            How many jobs to pull per role × location, in pages of 25. Higher means more
            results — but also more requests fired on <strong>your own</strong> LinkedIn
            account in one burst, which raises rate-limit / account risk. Keep it modest.
          </div>
        </div>
        <input
          type="number"
          min={LI_LIMIT_MIN}
          max={LI_LIMIT_MAX}
          step={25}
          value={limit}
          data-testid="linkedin-jobsearch-limit"
          onChange={(e) => {
            const n = Number(e.target.value) || LI_LIMIT_MIN;
            onChangeLimit(Math.max(LI_LIMIT_MIN, Math.min(LI_LIMIT_MAX, n)));
          }}
          className="h-[30px] w-20 rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink"
        />
      </div>
      {search.isSuccess ? (
        <div className="text-[11.5px] text-good" data-testid="linkedin-jobsearch-started">
          Search started — new matches will appear in the Job Board shortly.
        </div>
      ) : null}
      {search.isError ? (
        <div className="text-[11.5px] text-bad" data-testid="linkedin-jobsearch-error">
          {search.error instanceof Error ? search.error.message : "Search failed."}
        </div>
      ) : null}
    </div>
  );
}
