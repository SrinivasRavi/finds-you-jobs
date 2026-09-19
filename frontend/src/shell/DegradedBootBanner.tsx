import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useResumeScheduler, useSchedulerStatus } from "../api/queries";

/** The degraded-boot notice (D27).
 *
 * The shell keeps the last 5 boot outcomes; 3 in a row ending the same bad way
 * start the backend without its scheduler, so nothing queues itself into
 * whatever killed them. Without this banner the app would simply look idle,
 * which is the one thing a paused-work state must never do. Dismissible on
 * purpose: the pause is safe, and a blocking dialog is backlog S-C43.
 */
export function DegradedBootBanner() {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(false);
  const { data } = useSchedulerStatus();
  const resume = useResumeScheduler();

  // Only a degraded boot that is still paused has anything to say. Pressing
  // Resume flips `running`, which takes the banner away on its own.
  if (dismissed || !data?.degradedBoot || data.running) return null;

  return (
    <div
      className="flex items-center gap-3 border-b border-warn bg-warn-wash px-4 py-2 text-[12.5px] text-warn"
      data-testid="degraded-boot-banner"
    >
      <span className="min-w-0 flex-1">{t("shell.degradedBoot")}</span>
      <button
        type="button"
        className="shrink-0 rounded border border-warn px-2 py-0.5 text-[12px] disabled:opacity-60"
        onClick={() => resume.mutate()}
        disabled={resume.isPending}
      >
        {t("shell.degradedBootResume")}
      </button>
      <button
        type="button"
        className="shrink-0 text-[12px] underline"
        onClick={() => setDismissed(true)}
      >
        {t("shell.degradedBootDismiss")}
      </button>
    </div>
  );
}
