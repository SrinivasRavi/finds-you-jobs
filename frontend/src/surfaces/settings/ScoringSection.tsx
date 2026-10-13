// Scoring section: every scanned job is scored; the choice is how. An AI
// failure falls back to a grey keyword score, retryable in Logs. Memoized.

import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  ScoreWeightPreset,
  ScoringMethod,
  ScoringMode,
  Settings as SettingsT,
} from "../../api/types";
import type { components } from "../../api/schema";
import { WEIGHTED_AXES } from "../../api/types";
import { InfoDot } from "../../shell/InfoDot";
import { Section } from "./shared";

// The methods that cost money per job, and so the ones a per-tick cap is for.
// It used to show for `llm` alone, which was right when AI scoring was the only
// paid method; Fast scoring also bills per job, so a first scan of 1,000 jobs
// could run uncapped on it (maintainer, 2026-10-07).
const METERED_METHODS: ScoringMode[] = ["llm", "jev"];

// Batch cap presets (audit P1-1): the `score_new` tick scores every unscored job
// by default (0 = uncapped), so a large first scan can cost a lot at once. These
// write `thresholds.score_new_batch`, which the planner already reads.
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

// Menu order, richest explanation first, which is also roughly most expensive
// first. The server reports readiness per method and the copy lives here, so a
// build that ships a new method does not need a server string.
const MENU_ORDER: ScoringMode[] = ["llm", "jev", "local", "keyword"];

// The NAME of each method lives in `common.scoreMethod`, because the board's
// score ring names them too and a menu that disagrees with the board about
// what a method is called is worse than either name alone. The blurb and the
// price stay here: this menu is their only reader.
const COPY: Record<ScoringMode, { name: string; blurb: string; price: string }> = {
  llm: {
    name: "scoreMethod.llm",
    blurb: "settingsPage.scoring.modeLlmBlurb",
    price: "settingsPage.scoring.modeLlmPrice",
  },
  jev: {
    name: "scoreMethod.jev",
    blurb: "settingsPage.scoring.modeJevBlurb",
    price: "settingsPage.scoring.modeJevPrice",
  },
  local: {
    name: "scoreMethod.local",
    blurb: "settingsPage.scoring.modeLocalBlurb",
    price: "settingsPage.scoring.modeLocalPrice",
  },
  keyword: {
    name: "scoreMethod.keyword",
    blurb: "settingsPage.scoring.modeKeywordBlurb",
    price: "settingsPage.scoring.modeKeywordPrice",
  },
};

// Weight presets (scoring.md 7.4). The taste is the preset; the sliders are the
// escape hatch. Shipping only sliders would turn this pane into 5 numbers with
// no opinion in them, and shipping only presets would be an opinion with no
// escape, so both, with the sliders folded away.
const PRESETS: ScoreWeightPreset[] = [
  "balanced",
  "location_first",
  "seniority_first",
  "skills_only",
];

// The sidecar's constants as literal types (sidecar/app/shared_constants.py).
type Shared = components["schemas"]["SharedConstants"];

/** What each preset sets: `weights.PRESETS`, checked against it. `balanced` is empty
 *  because it means "whatever table the method that ran shipped with", so the
 *  sliders show that method's own numbers rather than a second opinion. */
const PRESET_WEIGHTS: Record<ScoreWeightPreset, Record<string, number>> = {
  balanced: {},
  location_first: {},
  seniority_first: {
    hard_requirements: 0.25,
    core_stack: 0.25,
    years: 0.1,
    level: 0.35,
    nice_to_haves: 0.05,
  },
  skills_only: {
    hard_requirements: 0.45,
    core_stack: 0.55,
    years: 0,
    level: 0,
    nice_to_haves: 0,
  },
} satisfies Shared["preset_weights"];

// The shipped table a `balanced` install actually scores with (`JEV_WEIGHTS`),
// so the sliders open showing real numbers instead of 5 zeroes.
const BALANCED_FALLBACK: Record<string, number> = {
  hard_requirements: 0.3,
  core_stack: 0.3,
  years: 0.15,
  level: 0.15,
  nice_to_haves: 0.1,
} satisfies Shared["jev_weights"];

function WeightControls({
  settings,
  patch,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const custom = settings.score_weights ?? {};
  const customised = Object.keys(custom).length > 0;
  // An older sidecar sends no preset at all, and a pane that throws on a
  // missing field takes the whole Settings screen with it.
  const preset = PRESETS.includes(settings.score_weight_preset)
    ? settings.score_weight_preset
    : "balanced";
  const presetTable = PRESET_WEIGHTS[preset];
  const shown = customised
    ? custom
    : Object.keys(presetTable).length > 0
      ? presetTable
      : BALANCED_FALLBACK;
  const dirty = customised || preset !== "balanced";
  return (
    <div data-testid="score-weights">
      <div className="flex items-center gap-2">
        <div className="flex flex-1 items-center text-[13px] font-medium text-ink">
          {t("settingsPage.scoring.weightsTitle")}
          <InfoDot label={t("settingsPage.scoring.weightsTitle")}>
            {t("settingsPage.scoring.weightsInfo")}
          </InfoDot>
        </div>
        {/* Always visible, never only inside the disclosure: weights are the
            easiest setting in this app to get lost in. */}
        <button
          data-testid="score-weights-reset"
          disabled={!dirty}
          onClick={() => patch({ score_weight_preset: "balanced" })}
          className={
            "rounded-7 px-2 py-1 text-[12px] " +
            (dirty ? "text-accent hover:bg-surface-3" : "text-ink-4")
          }
        >
          {t("settingsPage.scoring.weightsReset")}
        </button>
      </div>
      <div className="mb-2 text-[12px] text-ink-3">
        {t("settingsPage.scoring.weightsHint")}
      </div>
      <div className="flex flex-wrap gap-1.5" data-testid="score-weight-presets">
        {PRESETS.map((chip) => (
          <button
            key={chip}
            data-testid={`score-weight-preset-${chip}`}
            aria-pressed={!customised && preset === chip}
            onClick={() => patch({ score_weight_preset: chip })}
            className={
              "rounded-7 border px-2.5 py-1 text-[12px] " +
              (!customised && preset === chip
                ? "border-accent bg-accent text-white"
                : "border-border-2 bg-surface text-ink-2 hover:bg-surface-3")
            }
          >
            {t(`settingsPage.scoring.preset_${chip}`)}
          </button>
        ))}
      </div>
      <button
        data-testid="score-weights-disclosure"
        onClick={() => setOpen((v) => !v)}
        className="mt-2 text-[12px] text-accent hover:underline"
      >
        {open
          ? t("settingsPage.scoring.weightsHide")
          : t("settingsPage.scoring.weightsShow")}
      </button>
      {open ? (
        <div className="mt-2 space-y-2 rounded-md border border-border bg-surface p-3">
          {WEIGHTED_AXES.map((axis) => (
            <label key={axis} className="flex items-center gap-3 text-[12px]">
              <span className="w-36 shrink-0 text-ink-2">{t(`scoreFactor.${axis}`)}</span>
              <input
                type="range"
                data-testid={`score-weight-${axis}`}
                min={0}
                max={100}
                step={5}
                value={Math.round((shown[axis] ?? 0) * 100)}
                onChange={(e) =>
                  patch({
                    score_weights: {
                      ...Object.fromEntries(
                        WEIGHTED_AXES.map((a) => [a, shown[a] ?? 0]),
                      ),
                      [axis]: Number(e.target.value) / 100,
                    },
                  })
                }
                className="flex-1 accent-accent"
              />
              <span className="w-10 shrink-0 text-right tabular-nums text-ink-3">
                {Math.round((shown[axis] ?? 0) * 100)}%
              </span>
            </label>
          ))}
          <label className="flex items-center gap-3 border-t border-border pt-2 text-[12px]">
            <span className="w-36 shrink-0 text-ink-2">
              {t("settingsPage.scoring.locationPenalty")}
            </span>
            <input
              type="range"
              data-testid="score-location-penalty"
              min={0}
              max={50}
              step={5}
              value={settings.score_location_penalty ?? 15}
              onChange={(e) => patch({ score_location_penalty: Number(e.target.value) })}
              className="flex-1 accent-accent"
            />
            <span className="w-10 shrink-0 text-right tabular-nums text-ink-3">
              −{settings.score_location_penalty ?? 15}
            </span>
          </label>
          <p className="text-[11px] text-ink-3">
            {t("settingsPage.scoring.locationPenaltyHint")}
          </p>
        </div>
      ) : null}
    </div>
  );
}

/** Every method, in menu order, whatever the server sent. A method missing from
 *  the payload (an older sidecar) still renders, because hiding a row is the one
 *  thing this menu must never do. */
function methodRows(methods: ScoringMethod[]): ScoringMethod[] {
  const byId = new Map(methods.map((m) => [m.id, m]));
  return MENU_ORDER.map((id) => byId.get(id) ?? { id, ready: true, needs: null });
}

function MethodRow({
  method,
  selected,
  onPick,
  onAddKey,
}: {
  method: ScoringMethod;
  selected: boolean;
  onPick: () => void;
  onAddKey?: () => void;
}) {
  const { t } = useTranslation();
  const copy = COPY[method.id];
  return (
    <div
      data-testid={`scoring-mode-${method.id}-row`}
      className={
        "rounded-md border px-3 py-2 " +
        (selected
          ? "border-accent bg-accent-wash"
          : "border-border-2 bg-surface hover:bg-surface-3")
      }
    >
      {/* The picker IS this button, which is why the testid and `data-on` live on
          it rather than on the row: an action below must never become the thing a
          click lands on. A missing prerequisite never disables it either, because
          the app clears the prerequisite instead (talk.md D44). */}
      <button
        type="button"
        data-testid={`scoring-mode-${method.id}`}
        data-on={selected}
        data-ready={method.ready}
        onClick={onPick}
        aria-pressed={selected}
        className="w-full text-left"
      >
        <div className="flex items-baseline justify-between gap-3">
          <span
            className={
              "text-[13px] font-medium " + (selected ? "text-accent-ink" : "text-ink")
            }
          >
            {t(copy.name)}
          </span>
          <span className="shrink-0 text-[11.5px] text-ink-3">{t(copy.price)}</span>
        </div>
        <div className="text-[12px] text-ink-2">{t(copy.blurb)}</div>
        {method.needs === "model_pack" && (
          <div
            className="mt-1 text-[11.5px] text-ink-3"
            data-testid={`scoring-needs-${method.id}`}
          >
            <span className="font-medium">{t("settingsPage.scoring.needsPackLabel")}</span>{" "}
            {t("settingsPage.scoring.needsPackBody")}
          </div>
        )}
      </button>
      {method.needs === "openrouter_key" && (
        <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-ink-3">
          <span data-testid={`scoring-needs-${method.id}`}>
            {t("settingsPage.scoring.needsKeyLabel")}
          </span>
          {onAddKey && (
            <button
              type="button"
              data-testid="scoring-add-key"
              onClick={onAddKey}
              className="rounded-7 border border-border px-2 py-0.5 text-ink-2 hover:bg-surface-3"
            >
              {t("settingsPage.scoring.needsKeyAction")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export const ScoringSection = memo(function ScoringSection({
  settings,
  patch,
  onAddKey,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
  /** Jumps to the LLM providers pane, so "Add a key" is one click from here. */
  onAddKey?: () => void;
}) {
  const { t } = useTranslation();
  return (
    // No title: the category header already says "Scoring", and the card
    // repeating it was 2 headings deep for 1 thing (2026-10-08 split).
    <Section>
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
            {methodRows(settings.scoring_methods).map((method) => (
              <MethodRow
                key={method.id}
                method={method}
                selected={settings.scoring_mode === method.id}
                onPick={() => patch({ scoring_mode: method.id })}
                onAddKey={onAddKey}
              />
            ))}
          </div>
        </div>
        {/* Where the AI rubric is edited, said once, here, because this is the
            pane where a user decides scoring is wrong and goes looking. The
            other 3 methods have no prompt to edit (maintainer, 2026-10-07). */}
        {settings.scoring_mode === "llm" ? (
          <p className="text-[12px] text-ink-3" data-testid="scoring-prompt-pointer">
            {t("settingsPage.scoring.rubricPointer")}
          </p>
        ) : null}
        <WeightControls settings={settings} patch={patch} />
        {METERED_METHODS.includes(settings.scoring_mode) ? (
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
  );
});
