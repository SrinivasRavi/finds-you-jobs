// The account-safety consent scaffold shared by BOTH experimental LinkedIn
// opt-ins (Referral Outreach + LinkedIn job search). Extracted 2026-08-02
// (duplication audit D-F3): the scaffold was pasted twice, so an ack-semantics
// fix could land on one opt-in and silently miss the other — the gate-hole
// class. The consent MECHANICS now live here exactly once: the hazard badge,
// the ToS risk line, the acknowledgement checkbox, the toggle that stays inert
// until that box is ticked, the durable ack timestamp stamped on enable, and
// the ack reset on disable. Everything feature-specific — copy, testids, which
// settings keys persist, what unlocks below the toggle — is a prop.
//
// `ack` is owned by the caller on purpose: Referral Outreach lifts it to the
// Settings root so it survives pane switches, LinkedIn job search keeps it
// local so it resets on unmount. Only the storage differs; the semantics above
// are shared.

import { useTranslation } from "react-i18next";

import { InfoDot } from "../../shell/InfoDot";
import { ExperimentalHazard, LinkedInRiskLine, Section, Toggle } from "./shared";

export function LinkedInOptIn({
  title,
  intro,
  howLabel,
  howInfo,
  warning,
  ackLabel,
  ackTestid,
  ack,
  onAck,
  enableLabel,
  enabled,
  onEnable,
  onDisable,
  toggleTestid,
  ackAt,
  ackAtTestid,
  lockedHint,
  children,
}: {
  title: string;
  intro: string;
  howLabel: string;
  howInfo: React.ReactNode;
  warning: string;
  ackLabel: string;
  ackTestid: string;
  ack: boolean;
  onAck: (v: boolean) => void;
  enableLabel: string;
  enabled: boolean;
  /** Persist the opt-in ON, stamping the durable ack timestamp we hand you. */
  onEnable: (ackAt: string) => void;
  /** Persist the opt-in OFF (the ack checkbox is reset here, not by you). */
  onDisable: () => void;
  toggleTestid: string;
  ackAt: string | null;
  ackAtTestid: string;
  /** Shown in place of `children` while the opt-in is off. */
  lockedHint?: string;
  /** The feature itself — rendered only once the opt-in is on. */
  children?: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Section title={title} titleExtra={<ExperimentalHazard />}>
      <div className="space-y-3">
        <p className="text-[12.5px] text-ink-2">
          {intro}
          <InfoDot label={howLabel}>{howInfo}</InfoDot>
        </p>
        <LinkedInRiskLine detail={warning} />
        <label className="flex items-start gap-2 text-[12px] font-medium text-ink-2">
          <input
            type="checkbox"
            checked={ack || enabled}
            onChange={(e) => onAck(e.target.checked)}
            data-testid={ackTestid}
            className="mt-0.5"
          />
          {ackLabel}
        </label>
        <div className="flex items-center gap-3">
          <div className="flex-1 text-[13px] font-medium text-ink">{enableLabel}</div>
          <Toggle
            on={enabled}
            onChange={(v) => {
              // The gate: turning it ON is inert until the box above is ticked.
              if (v && !ack) return;
              if (v) {
                // Durable ack record (audit P2-5): the checkbox above is
                // ephemeral local state (resets on disable); this timestamp
                // persists to ui_state so re-opening Settings shows *when*
                // the ToS risk was last accepted, not just the live toggle.
                onEnable(new Date().toISOString());
              } else {
                onDisable();
                onAck(false);
              }
            }}
            testid={toggleTestid}
          />
        </div>
        {ackAt ? (
          <div className="text-[11px] text-ink-4" data-testid={ackAtTestid}>
            {t("settingsPage.acknowledgedOn", {
              date: new Date(ackAt).toLocaleDateString(),
            })}
          </div>
        ) : null}
        {enabled ? (
          children
        ) : lockedHint ? (
          <div className="text-[11.5px] text-ink-4">{lockedHint}</div>
        ) : null}
      </div>
    </Section>
  );
}
