import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useResumeScheduler, useSchedulerStatus } from "../api/queries";
import { Modal } from "./Modal";

/** The degraded-boot notice (D27).
 *
 * The shell keeps the last 5 boot outcomes; 3 in a row ending the same bad way
 * start the backend without its scheduler, so nothing queues itself into
 * whatever killed them. Without this banner the app would simply look idle,
 * which is the one thing a paused-work state must never do. It opens as a
 * dialog that must be answered (S-C43); keeping work paused leaves the banner
 * up, so the state stays visible until the user resumes or dismisses it.
 */
export function DegradedBootBanner() {
  const { t } = useTranslation();
  const [answered, setAnswered] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const { data } = useSchedulerStatus();
  const resume = useResumeScheduler();

  // Only a degraded boot that is still paused has anything to say. Pressing
  // Resume flips `running`, which takes the banner away on its own.
  if (dismissed || !data?.degradedBoot || data.running) return null;

  if (!answered) {
    return (
      <Modal title={t("shell.degradedBootTitle")} onClose={() => setAnswered(true)} size="sm">
        <div className="flex flex-col gap-4 px-5 py-4" data-testid="degraded-boot-dialog">
          <p className="text-[13px] text-ink-2">{t("shell.degradedBootBody")}</p>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setAnswered(true)}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              {t("shell.degradedBootKeepPaused")}
            </button>
            <button
              type="button"
              onClick={() => resume.mutate()}
              disabled={resume.isPending}
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:opacity-60"
            >
              {t("shell.degradedBootResume")}
            </button>
          </div>
        </div>
      </Modal>
    );
  }

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
