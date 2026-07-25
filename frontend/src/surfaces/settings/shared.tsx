// Shared building blocks for the Settings panes (extracted from Settings.tsx
// 2026-07-25, F-M6 monolith split — pure moves, zero behavior change).

import { useTranslation } from "react-i18next";

import { InfoDot } from "../../shell/InfoDot";

// Muted warn styling (2026-07-23): the dark-theme `warn-wash` (#78350f) reads as
// a loud brown; a light amber tint is calmer and consistent across every tab.
export const MUTED_WARN_BOX = "rounded-lg border border-warn/30 bg-warn/5 text-warn";
export const MUTED_WARN_PILL =
  "inline-flex cursor-help items-center gap-1 rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-[10px] font-semibold text-warn";

// The two LinkedIn features (Referral Outreach, LinkedIn job search) both drive
// your logged-in session and both break LinkedIn's ToS — same hazard marker,
// same shared session, separate opt-ins.
const LINKEDIN_HAZARD_TIP = "settingsPage.linkedinHazardTip";

export function ExperimentalHazard() {
  const { t } = useTranslation();
  return (
    <span data-testid="experimental-hazard" title={t(LINKEDIN_HAZARD_TIP)} className={MUTED_WARN_PILL}>
      <span aria-hidden="true">⚠</span> {t("settingsPage.experimental")}
    </span>
  );
}

// The short warn-tinted risk line + an "i" to the full text — replaces the wall
// of warning copy on both LinkedIn opt-ins (2026-07-23: brief in place, detail
// one click away). `detail` differs per feature (messaging vs scraping).
export function LinkedInRiskLine({ detail }: { detail: string }) {
  const { t } = useTranslation();
  return (
    <div className={"flex items-start gap-1 px-3 py-2 text-[11.5px] leading-relaxed " + MUTED_WARN_BOX}>
      <span>{t("settingsPage.riskLine")}</span>
      <InfoDot label={t("settingsPage.riskDetailLabel")}>{detail}</InfoDot>
    </div>
  );
}

export function Toggle({
  on,
  onChange,
  testid,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  testid?: string;
}) {
  return (
    <button
      role="switch"
      aria-checked={on}
      data-testid={testid}
      onClick={() => onChange(!on)}
      className={
        "relative inline-block h-5 w-9 shrink-0 rounded-full transition-colors " +
        (on ? "bg-accent" : "bg-border-2")
      }
    >
      <span
        className={
          "absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all " +
          (on ? "left-[18px]" : "left-0.5")
        }
      />
    </button>
  );
}

// `title` is optional: a pane whose header already names the content (e.g.
// Appearance) renders the card alone instead of repeating itself.
export function Section({
  title,
  titleExtra,
  children,
}: {
  title?: string;
  titleExtra?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      {title ? (
        <div className="flex items-center gap-2">
          <h2 className="text-[13px] font-semibold text-ink">
            {title}
          </h2>
          {titleExtra}
        </div>
      ) : null}
      <div className="rounded-xl border border-border bg-surface p-4">{children}</div>
    </section>
  );
}
