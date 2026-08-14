// ─── Contact & data lifecycle (FR-SYS-06 / FR-NW-15) ────────────────────────
// One editable row per configurable window. `unit` is days unless noted; a
// blank/zero input falls back to the stored value (the backend clamps too).
// (Extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure move,
// zero behavior change.) Memoized: `settings` is the query-stable object and
// `patch` is a root-stable useCallback.

import { memo } from "react";
import { Trans, useTranslation } from "react-i18next";

import type { Settings as SettingsT } from "../../api/types";
import { Section } from "./shared";

function LifecycleRow({
  label,
  hint,
  unit,
  value,
  onChange,
  testid,
  options,
}: {
  label: string;
  hint: string;
  unit: string;
  value: number;
  onChange: (v: number) => void;
  testid: string;
  /** Fixed choices instead of a free number input (can't be typo'd). */
  options?: number[];
}) {
  return (
    <div className="flex items-center gap-3" data-testid={`lifecycle-${testid}-row`}>
      <div className="flex-1">
        <div className="text-[13px] font-medium text-ink">{label}</div>
        <div className="text-[12px] text-ink-3">{hint}</div>
      </div>
      <div className="flex items-center gap-2">
        {options ? (
          <select
            value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            data-testid={`lifecycle-${testid}`}
            className="rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink"
          >
            {options.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        ) : (
          <input
            type="number"
            min={1}
            value={value}
            onChange={(e) => onChange(Number(e.target.value) || 0)}
            data-testid={`lifecycle-${testid}`}
            className="w-20 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink"
          />
        )}
        <span className="text-[11.5px] text-ink-3">{unit}</span>
      </div>
    </div>
  );
}

export const LifecycleSection = memo(function LifecycleSection({
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
        {/* Fixed choices, not a free number (maintainer 2026-08-02). */}
        <LifecycleRow
          label={t("settingsPage.lifecycle.expireListingLabel")}
          hint={t("settingsPage.lifecycle.expireListingHint")}
          unit={t("settingsPage.lifecycle.days")}
          value={lc.expire_listing_days}
          onChange={set("expire_listing_days")}
          testid="expire-listing"
          options={[7, 14, 30, 60]}
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
        {/* The contact-sync cadence control is gone with the schedule itself:
            syncing is user-initiated only (the Sync button / on-open refresh —
            docs/internal/linkedin-addon.md section 5). */}
      </div>
    </Section>
  );
});
