// The engine + model picker for one LLM operation kind (url-less-jobs.md
// section 11.5). It used to live inside the prompt editor, so only the 4 kinds
// that happened to have an editable prompt got a picker and the 2 that didn't
// (referral drafting, the Applier) were set once at onboarding and invisible
// forever. It's keyed on the operation kind now, so any section can carry the
// picker for the work it owns.

import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { OperationKind, Settings as SettingsT } from "../../api/types";
import { AI_OFF, CLAUDE_CLI_DEFAULT_MODEL, CLI_ENGINE_OPTIONS, isCliEngine } from "./engines";

/** The engine a kind routes to, and the model that engine will actually use
 *  when the kind names none. Exported for the prompt-test modal, which states
 *  the same pair before a run. */
export function useRoute(settings: SettingsT, kind: string) {
  const { t } = useTranslation();
  const route = settings.routing.find((r) => r.kind === kind);
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
    (engine === AI_OFF && t("settingsPage.prompts.engineOff")) ||
    (cliEngineLabel && t(cliEngineLabel)) ||
    settings.providers.find((p) => p.id === engine)?.label ||
    engine;
  return { route, engine, effectiveModel, engineLabel };
}

export const ModelPicker = memo(function ModelPicker({
  kind,
  settings,
  patch,
  hint,
}: {
  /** The LLM operation this picker routes (`LLM_KINDS`): score, tailor, cover,
   *  extract, draft or apply. */
  kind: OperationKind;
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
  /** One line saying what this model does, above the controls. */
  hint?: string;
}) {
  const { t } = useTranslation();
  const [modelDraft, setModelDraft] = useState<string | null>(null);
  const { route, engine, effectiveModel, engineLabel } = useRoute(settings, kind);
  const options = [
    // Shown only while the kind is off, so the select names the state it's in.
    ...(engine === AI_OFF ? [{ id: AI_OFF, label: t("settingsPage.prompts.engineOff") }] : []),
    ...CLI_ENGINE_OPTIONS.map((o) => ({ id: o.id, label: t(o.label) })),
    ...settings.providers.filter((p) => p.configured).map((p) => ({ id: p.id, label: p.label })),
  ];

  function route_to(engineId: string, model: string) {
    patch({
      routing: [
        ...settings.routing.filter((r) => r.kind !== kind),
        { kind, engine: engineId, model },
      ],
    });
  }

  return (
    <div className="space-y-2" data-testid={`model-picker-${kind}`}>
      {hint ? <p className="text-[11.5px] leading-relaxed text-ink-3">{hint}</p> : null}
      <div className="flex items-center gap-3">
        <span className="text-[11.5px] text-ink-3">{t("settingsPage.prompts.modelEngine")}</span>
        <select
          value={engine}
          data-testid={`route-${kind}`}
          onChange={(e) => {
            // Changing the engine clears the per-kind model so the engine's own
            // default applies.
            setModelDraft(null);
            route_to(e.target.value, "");
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
          data-testid={`route-${kind}-model`}
          title={t("settingsPage.prompts.modelTitle", { engineLabel, effectiveModel })}
          onChange={(e) => setModelDraft(e.target.value)}
          onBlur={() => {
            if (modelDraft == null || modelDraft === (route?.model || "")) return;
            route_to(engine, modelDraft);
            setModelDraft(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          className="w-56 truncate rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink-2"
        />
      </div>
    </div>
  );
});
