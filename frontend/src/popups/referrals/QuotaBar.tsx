// Quota / status bar (extracted from ReferralsModal.tsx 2026-07-25, F-M6
// monolith split — pure move, zero behavior change). Memoized: props are
// primitives plus the query-stable quota object.

import { memo } from "react";
import { Trans, useTranslation } from "react-i18next";

import type { ReferralQuota } from "../../api/types";

export const QuotaBar = memo(function QuotaBar({
  alreadyReached,
  connected,
  quota,
  capReached,
  dailyRemaining,
}: {
  alreadyReached: number;
  connected: boolean;
  quota: ReferralQuota | undefined;
  capReached: boolean;
  dailyRemaining: number;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="flex flex-col gap-1 border-b border-border bg-surface-2 px-5 py-2 text-[11.5px]"
      data-testid="referrals-quota-bar"
    >
      <div className="flex items-center gap-3">
        <span data-testid="referrals-quota-counter" className="font-mono text-ink-2">
          {t("popups.referrals.reachesSent", { count: alreadyReached })}
        </span>
        {/* Our conservative caps only apply when WE do the sending
            (automation on + connected). In manual mode the user tracks
            their own outreach against LinkedIn's real limits. */}
        {quota && connected ? (
          <>
            <span className="text-ink-4">·</span>
            <span
              className="text-ink-3"
              title={t("popups.referrals.quotaTooltip")}
            >
              <Trans
                i18nKey="popups.referrals.automatedQuota"
                values={{
                  dailyUsed: quota.daily_used,
                  dailyLimit: quota.daily_limit,
                  weeklyUsed: quota.weekly_used,
                  weeklyLimit: quota.weekly_limit,
                }}
                components={{ strong: <strong /> }}
              />
            </span>
            <span className="text-ink-4">·</span>
            <span
              className="text-ink-3"
              data-testid="referrals-dm-counter"
              title={t("popups.referrals.dmTooltip")}
            >
              <Trans
                i18nKey="popups.referrals.dmCounter"
                values={{ dmSent: quota.dm_daily_sent }}
                components={{ strong: <strong /> }}
              />
            </span>
          </>
        ) : (
          <>
            <span className="text-ink-4">·</span>
            <span className="text-ink-3">{t("popups.referrals.manualModeQuota")}</span>
          </>
        )}
      </div>
      {capReached && (
        <div className="rounded-md border border-bad bg-bad-wash px-3 py-1.5 font-medium text-bad" data-testid="quota-blocked">
          {t("popups.referrals.dailyLimitReached")}
        </div>
      )}
      {!capReached && dailyRemaining <= 5 && (
        <div className="rounded-md border border-warn bg-warn-wash px-3 py-1.5 font-medium text-warn">
          {t("popups.referrals.closeToLimit", { count: dailyRemaining })}
        </div>
      )}
    </div>
  );
});
