// Referral Outreach risk toggle — the canonical feature name for the automated
// LinkedIn module (maintainer, 2026-07-10). The Networking tab (contact CRM +
// kanban + manual tracking) is ALWAYS available and carries no risk; this gates
// only the automated actions. This section is deliberately the feature's ONE
// reveal point (it is never advertised elsewhere), so the copy carries the full
// context. (Extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure
// moves, zero behavior change. The `ack` checkbox state stays at the Settings
// root so it survives pane switches exactly as before.) Not memoized: `ack` /
// `onAck` change with root state by design.
//
// The consent scaffold itself (hazard badge, risk line, ack checkbox, gated
// toggle, durable ack timestamp) is `LinkedInOptIn` — shared with the LinkedIn
// job-search opt-in since 2026-08-02 (duplication audit D-F3), so an
// ack-semantics fix can no longer land on one opt-in and miss the other.

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
