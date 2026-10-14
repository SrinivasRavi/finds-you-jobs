// Referral Outreach risk toggle: gates only the automated LinkedIn actions; the
// Networking CRM is always available. This is the feature's 1 reveal point, so the
// copy carries the full context. `ack` lives at the Settings root to survive pane
// switches, so it isn't memoized; the scaffold is `LinkedInOptIn` (D-F3).

import { Trans, useTranslation } from "react-i18next";

import type { Settings as SettingsT } from "../../api/types";
import { LinkedInOptIn } from "./LinkedInOptIn";
import { LinkedInSessionSection } from "./LinkedInSections";

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
    <LinkedInOptIn
      title={t("settingsPage.referral.title")}
      intro={t("settingsPage.referral.intro")}
      howLabel={t("settingsPage.referral.howLabel")}
      howInfo={<Trans i18nKey="settingsPage.referral.howInfo" components={{ em: <em /> }} />}
      warning={t(NETWORKING_WARNING)}
      ackLabel={t("settingsPage.referral.ack")}
      ackTestid="networking-ack"
      ack={ack}
      onAck={onAck}
      enableLabel={t("settingsPage.referral.enable")}
      enabled={settings.networking_enabled}
      onEnable={(ackAt) => patch({ networking_enabled: true, networking_ack_at: ackAt })}
      onDisable={() => patch({ networking_enabled: false })}
      toggleTestid="networking-toggle"
      ackAt={settings.networking_ack_at}
      ackAtTestid="networking-ack-at"
      lockedHint={t("settingsPage.referral.lockedHint")}
    >
      {/* Step 2 — the LinkedIn session (US-SET-06) lives INSIDE this
          experimental section (2026-07-17 dogfood: shown separately it
          read as an unrelated, non-experimental setting and the user
          never connected). Rendered only when the toggle is on. */}
      <LinkedInSessionSection />
    </LinkedInOptIn>
  );
}
