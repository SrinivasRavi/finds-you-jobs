// Scoring pane section: a scanned job is scored before anything else happens
// to it. Every scanned job is scored; the choice is HOW. AI failures fall back
// to a grey keyword score (retry in Logs). (Extracted from Settings.tsx
// 2026-07-25, F-M6 monolith split — pure moves, zero behavior change.)
// Memoized: `settings` is the query-stable object; `patch` is a root
// useCallback.

import { memo } from "react";
import { useTranslation } from "react-i18next";

import type { ScoringMethod, ScoringMode, Settings as SettingsT } from "../../api/types";
import { InfoDot } from "../../shell/InfoDot";
import { Section } from "./shared";

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

// Menu order, richest explanation first, which is also roughly most expensive
// first. The server reports readiness per method and the copy lives here, so a
// build that ships a new method does not need a server string.
const MENU_ORDER: ScoringMode[] = ["llm", "jev", "local", "keyword"];

const COPY: Record<ScoringMode, { name: string; blurb: string; price: string }> = {
  llm: {
    name: "settingsPage.scoring.modeLlmName",
    blurb: "settingsPage.scoring.modeLlmBlurb",
    price: "settingsPage.scoring.modeLlmPrice",
  },
  jev: {
    name: "settingsPage.scoring.modeJevName",
    blurb: "settingsPage.scoring.modeJevBlurb",
    price: "settingsPage.scoring.modeJevPrice",
  },
  local: {
    name: "settingsPage.scoring.modeLocalName",
    blurb: "settingsPage.scoring.modeLocalBlurb",
    price: "settingsPage.scoring.modeLocalPrice",
  },
  keyword: {
    name: "settingsPage.scoring.modeKeywordName",
    blurb: "settingsPage.scoring.modeKeywordBlurb",
    price: "settingsPage.scoring.modeKeywordPrice",
  },
};

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
  );
});
