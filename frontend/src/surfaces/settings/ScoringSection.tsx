// Scoring pane section: a scanned job is scored before anything else happens
// to it. Every scanned job is scored; the choice is HOW. AI failures fall back
// to a grey keyword score (retry in Logs). (Extracted from Settings.tsx
// 2026-07-25, F-M6 monolith split — pure moves, zero behavior change.)
// Memoized: `settings` is the query-stable object; `patch`/`onPickMode` are
// root useCallbacks.

import { memo } from "react";
import { useTranslation } from "react-i18next";

import type { Settings as SettingsT } from "../../api/types";
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

export const ScoringSection = memo(function ScoringSection({
  settings,
  patch,
  onPickMode,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
  onPickMode: (mode: SettingsT["scoring_mode"]) => void;
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
                onClick={() => onPickMode(mode)}
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
  );
});
