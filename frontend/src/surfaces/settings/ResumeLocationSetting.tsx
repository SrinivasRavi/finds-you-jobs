// Where the tailored resume and its cover letter say the user is (audit B3,
// maintainer 2026-10-13): 1 setting, `resume_location`, drawn in Settings ›
// Applying and Settings › Resume & cover letter. Both places say so, and each
// links to the other, so nobody hunts for a second copy that doesn't exist.

import { useTranslation } from "react-i18next";

import type { Settings as SettingsT } from "../../api/types";

export function ResumeLocationSetting({
  settings,
  patch,
  alsoIn,
  onGo,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
  /** The other category this same setting shows in. */
  alsoIn: "documents" | "applying";
  onGo: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div data-testid={`resume-location-${alsoIn === "applying" ? "documents" : "applying"}`}>
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <div className="text-[13px] font-medium text-ink">
            {t("settingsPage.applicant.resumeLocation")}
          </div>
          <div className="text-[12px] text-ink-3">
            {t("settingsPage.applicant.resumeLocationHint")}
          </div>
        </div>
        <select
          data-testid="resume-location-select"
          value={settings.resume_location}
          onChange={(e) =>
            patch({ resume_location: e.target.value as SettingsT["resume_location"] })
          }
          className="h-[30px] rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink"
        >
          <option value="master">{t("settingsPage.applicant.rl_master")}</option>
          <option value="posting">{t("settingsPage.applicant.rl_posting")}</option>
          <option value="none">{t("settingsPage.applicant.rl_none")}</option>
        </select>
      </div>
      <p className="mt-1.5 text-[11.5px] text-ink-4" data-testid="resume-location-shared">
        {t("settingsPage.applicant.resumeLocationShared", {
          other: t(`settingsNav.${alsoIn}`),
        })}{" "}
        <button type="button" onClick={onGo} className="text-accent hover:underline">
          {t("settingsPage.applicant.resumeLocationGo", { other: t(`settingsNav.${alsoIn}`) })}
        </button>
      </p>
    </div>
  );
}
