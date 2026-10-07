// ─── Engine routing + editable prompts (FR-SET-11) ──────────────────────────
// Each LLM operation is a collapsible row: header shows the engine/model
// summary + an "edited" badge; expanded reveals the engine selector (routed
// kinds only) and a monospace editor for that operation's system prompt (the
// module skill markdown), with Save/Reset. Collapsed by default so the large
// prompt text never overwhelms the page.
// (Extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure moves,
// zero behavior change.)

import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

import { usePrompts, useResetPrompt } from "../../api/queries";
import type { OperationKind, PromptSetting, Settings as SettingsT } from "../../api/types";
import { CLAUDE_CLI_DEFAULT_MODEL, CLI_ENGINE_OPTIONS, isCliEngine } from "./engines";
import { PromptTuningModal } from "./PromptTuningModal";

/** What each prompt actually governs, and what it does NOT.
 *
 *  The one that needed saying: `score` is the AI scoring rubric and nothing
 *  else. Keyword scoring is Python rules, Fast scoring sends typed questions
 *  carrying their own rubric bands, and On-device has no prompt either, so
 *  editing this file changes 1 of the 4 methods (maintainer, 2026-10-07). */
export const SCOPE_KEY: Record<string, string> = {
  score: "settingsPage.prompts.scopeScore",
  tailor: "settingsPage.prompts.scopeTailor",
  cover: "settingsPage.prompts.scopeCover",
  extract: "settingsPage.prompts.scopeExtract",
  networker_draft: "settingsPage.prompts.scopeNetworkerDraft",
};

/** The name of a prompt, wherever it is shown: the tab, the modal title, the
 *  pointer from the Scoring pane. `score` reads as the scoring method it
 *  belongs to, so the Scoring pane and this one can never call it 2 different
 *  things. An unknown kind falls back to whatever the sidecar called it. */
export const TITLE_KEY: Record<string, string> = {
  score: "scoreMethod.llm",
  tailor: "settingsPage.prompts.titleTailor",
  cover: "settingsPage.prompts.titleCover",
  extract: "settingsPage.prompts.titleExtract",
  networker_draft: "settingsPage.prompts.titleNetworkerDraft",
};

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
  const [modelDraft, setModelDraft] = useState<string | null>(null);
  const [tuning, setTuning] = useState(false);
  const resetPrompt = useResetPrompt();

  const edited = prompt.override_md != null;
  const text = prompt.override_md ?? prompt.default_md;

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

  function reset() {
    if (!window.confirm(t("settingsPage.prompts.resetConfirm"))) return;
    resetPrompt.mutate(prompt.kind);
  }

  const scopeKey = SCOPE_KEY[prompt.kind];
  return (
    <div className="flex flex-col gap-3">
      {scopeKey ? (
        <p
          data-testid={`prompt-scope-${prompt.kind}`}
          className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[11.5px] text-ink-2"
        >
          {t(scopeKey)}
        </p>
      ) : null}
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
      {/* Read-only here, editable in the modal. That IS the rule about which
          prompt runs where: a test run uses the draft, everything else uses
          this, and the boundary is a window rather than a sentence of copy
          (prompt-tuning.md section 3). */}
      <textarea
        value={text}
        readOnly
        spellCheck={false}
        data-testid={`prompt-textarea-${prompt.kind}`}
        className="h-[66vh] min-h-[360px] w-full resize-y rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-[12px] leading-relaxed text-ink-2"
      />
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-ink-4" data-testid={`prompt-chars-${prompt.kind}`}>
          {t("settingsPage.prompts.charCount", { n: text.length })}
          {edited
            ? t("settingsPage.prompts.overrideActive")
            : t("settingsPage.prompts.shippedDefault")}
        </span>
        <div className="flex items-center gap-2">
          {/* Reset stays OUT here, so the way back to what we ship is always 1
              click away and never behind the editor (maintainer, 2026-10-06). */}
          <button
            type="button"
            onClick={reset}
            disabled={!edited}
            data-testid={`prompt-reset-${prompt.kind}`}
            className="rounded-md border border-border px-2.5 py-1 text-[12px] text-ink-2 disabled:opacity-40"
          >
            {t("settingsPage.prompts.resetToDefault")}
          </button>
          {/* Outlined, not filled: the loudest control on this row would
              otherwise be the one that changes nothing on its own
              (maintainer, 2026-10-08). */}
          <button
            type="button"
            onClick={() => setTuning(true)}
            data-testid={`prompt-edit-${prompt.kind}`}
            className="rounded-md border border-accent px-2.5 py-1 text-[12px] font-medium text-accent-ink hover:bg-accent-wash"
          >
            {t("settingsPage.prompts.openEditor")}
          </button>
        </div>
      </div>
      {tuning ? (
        <PromptTuningModal
          prompt={prompt}
          name={TITLE_KEY[prompt.kind] ? t(TITLE_KEY[prompt.kind]) : prompt.title}
          onClose={() => setTuning(false)}
        />
      ) : null}
    </div>
  );
}

export const EngineRoutingSection = memo(function EngineRoutingSection({
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
              {TITLE_KEY[p.kind] ? t(TITLE_KEY[p.kind]) : p.title}
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
});
