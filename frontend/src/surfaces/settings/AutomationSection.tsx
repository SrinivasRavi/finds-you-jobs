// Automation on Save — split defaults (FR-SET-02): Resume ON, Cover ON.
// After Scoring in the workflow (maintainer 2026-07-23). (Extracted from
// Settings.tsx 2026-07-25, F-M6 monolith split — pure moves, zero behavior
// change.) Memoized: `settings` is the query-stable object, `patch` a root
// useCallback.

import { memo } from "react";
import { useTranslation } from "react-i18next";

import type { Settings as SettingsT } from "../../api/types";
import { InfoDot } from "../../shell/InfoDot";
import { Section, Toggle } from "./shared";

export const AutomationSection = memo(function AutomationSection({
  settings,
  patch,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  return (
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
            prep op (docs/internal/archived/applier-as-built.md section 2) — the agentic Applier
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
  );
});
