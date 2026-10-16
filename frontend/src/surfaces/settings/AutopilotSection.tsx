// Autopilot (audit-2026-10-14.md section 8.10): per action, the user picks
// "confirm each" (off) or autopilot (on). The sidecar refuses a background send
// or Submit on these columns, so this pane only reads and writes them.

import { memo, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Settings as SettingsT } from "../../api/types";
import { Section, Toggle } from "./shared";

type LimitKey = "autopilot_min_score" | "autopilot_applies_per_day" | "autopilot_contacts_per_job";

const LIMITS: { key: LimitKey; label: string; min: number; max: number }[] = [
  { key: "autopilot_min_score", label: "minScoreLabel", min: 0, max: 100 },
  { key: "autopilot_applies_per_day", label: "perDayLabel", min: 1, max: 100 },
  { key: "autopilot_contacts_per_job", label: "perJobLabel", min: 1, max: 10 },
];

/** A number saved on blur or Enter, clamped to the range the sidecar accepts,
 *  so typing "95" never posts "9" on the way. */
function LimitField({
  label,
  value,
  min,
  max,
  testid,
  onCommit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  testid: string;
  onCommit: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const n = Math.min(max, Math.max(min, Math.round(Number(draft) || min)));
    setDraft(String(n));
    if (n !== value) onCommit(n);
  };
  return (
    <label className="flex items-center justify-between gap-3 text-[13px] text-ink">
      {label}
      <input
        type="number"
        min={min}
        max={max}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
        data-testid={testid}
        className="w-20 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink"
      />
    </label>
  );
}

function SwitchRow({
  label,
  hint,
  on,
  disabled,
  testid,
  onChange,
}: {
  label: string;
  hint: string;
  on: boolean;
  disabled?: boolean;
  testid: string;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className={"flex items-center gap-3" + (disabled ? " opacity-50" : "")}>
      <div className="flex-1">
        <div className="text-[13px] font-medium text-ink">{label}</div>
        <div className="text-[12px] text-ink-3">{hint}</div>
      </div>
      <Toggle on={on} onChange={(v) => !disabled && onChange(v)} testid={testid} />
    </div>
  );
}

export const AutopilotSection = memo(function AutopilotSection({
  settings,
  patch,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  const outreachReady = settings.networking_enabled;
  return (
    // The pane header already says "Autopilot", so the card has no title.
    <Section>
      <div className="space-y-4" data-testid="autopilot-section">
        <p className="text-[12px] text-ink-4">{t("settingsPage.autopilot.intro")}</p>
        <SwitchRow
          label={t("settingsPage.autopilot.applyLabel")}
          hint={t("settingsPage.autopilot.applyHint", {
            min: settings.autopilot_min_score,
            perDay: settings.autopilot_applies_per_day,
          })}
          on={settings.autopilot_apply}
          testid="autopilot-apply-toggle"
          onChange={(v) => patch({ autopilot_apply: v })}
        />
        <SwitchRow
          label={t("settingsPage.autopilot.inviteLabel")}
          hint={t("settingsPage.autopilot.inviteHint", {
            perJob: settings.autopilot_contacts_per_job,
          })}
          on={settings.autopilot_invite}
          disabled={!outreachReady}
          testid="autopilot-invite-toggle"
          onChange={(v) => patch({ autopilot_invite: v })}
        />
        <SwitchRow
          label={t("settingsPage.autopilot.dmLabel")}
          hint={t("settingsPage.autopilot.dmHint")}
          on={settings.autopilot_dm}
          disabled={!outreachReady}
          testid="autopilot-dm-toggle"
          onChange={(v) => patch({ autopilot_dm: v })}
        />
        {!outreachReady ? (
          <p className="text-[12px] text-ink-3" data-testid="autopilot-needs-outreach">
            {t("settingsPage.autopilot.needsOutreach")}
          </p>
        ) : null}
        <div className="space-y-2 border-t border-border pt-3">
          {LIMITS.map((l) => (
            <LimitField
              key={l.key}
              label={t(`settingsPage.autopilot.${l.label}`)}
              value={settings[l.key]}
              min={l.min}
              max={l.max}
              testid={l.key.replace(/_/g, "-")}
              onCommit={(v) => patch({ [l.key]: v })}
            />
          ))}
        </div>
      </div>
    </Section>
  );
});
