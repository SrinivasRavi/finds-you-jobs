// Referral Outreach risk toggle — the canonical feature name for the automated
// LinkedIn module (maintainer, 2026-07-10). The Networking tab (contact CRM +
// kanban + manual tracking) is ALWAYS available and carries no risk; this gates
// only the automated actions. This section is deliberately the feature's ONE
// reveal point (it is never advertised elsewhere), so the copy carries the full
// context. (Extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure
// moves, zero behavior change. The `ack` checkbox state stays at the Settings
// root so it survives pane switches exactly as before.) Not memoized: `ack` /
// `onAck` change with root state by design.

import { Trans, useTranslation } from "react-i18next";

import type { Settings as SettingsT } from "../../api/types";
import { InfoDot } from "../../shell/InfoDot";
import { LinkedInSessionSection } from "./LinkedInSections";
import { ExperimentalHazard, LinkedInRiskLine, Section, Toggle } from "./shared";

const NETWORKING_WARNING = "settingsPage.referral.warning";

export function ReferralOutreachSection({
  settings,
  patch,
  ack,
  onAck,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
  ack: boolean;
  onAck: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Section title={t("settingsPage.referral.title")} titleExtra={<ExperimentalHazard />}>
      <div className="space-y-3">
        <p className="text-[12.5px] text-ink-2">
          {t("settingsPage.referral.intro")}
          <InfoDot label={t("settingsPage.referral.howLabel")}>
            <Trans i18nKey="settingsPage.referral.howInfo" components={{ em: <em /> }} />
          </InfoDot>
        </p>
        <LinkedInRiskLine detail={t(NETWORKING_WARNING)} />
        <label className="flex items-start gap-2 text-[12px] font-medium text-ink-2">
          <input
            type="checkbox"
            checked={ack || settings.networking_enabled}
            onChange={(e) => onAck(e.target.checked)}
            data-testid="networking-ack"
            className="mt-0.5"
          />
          {t("settingsPage.referral.ack")}
        </label>
        <div className="flex items-center gap-3">
          <div className="flex-1 text-[13px] font-medium text-ink">
            {t("settingsPage.referral.enable")}
          </div>
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
              if (!v) onAck(false);
            }}
            testid="networking-toggle"
          />
        </div>
        {settings.networking_ack_at ? (
          <div className="text-[11px] text-ink-4" data-testid="networking-ack-at">
            {t("settingsPage.acknowledgedOn", {
              date: new Date(settings.networking_ack_at).toLocaleDateString(),
            })}
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
            {t("settingsPage.referral.lockedHint")}
          </div>
        )}
      </div>
    </Section>
  );
}
