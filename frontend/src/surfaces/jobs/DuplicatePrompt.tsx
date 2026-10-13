// "This looks like the job you added on Monday" (url-less-jobs.md 9.6): a pasted
// JD and a later scan's row for the same posting. The app says so and stops: the
// user deletes one, and nothing is merged (section 5.5). "They're different"
// silences the notice for that pair.

import { useTranslation } from "react-i18next";

import { useDismissJobDuplicate, useJobDuplicate } from "../../api/queries";
import type { Job } from "../../api/types";
import { daysBetween } from "../jobFormat";

export function DuplicatePrompt({ job }: { job: Job }) {
  const { t } = useTranslation();
  const jobId = job.id;
  const { data } = useJobDuplicate(jobId, job.duplicate_notice);
  const dismiss = useDismissJobDuplicate();
  if (!data) return null;
  const raw = daysBetween(data.other_added_at, "round");
  const days = Number.isFinite(raw) ? Math.max(0, raw) : 0;
  // "0 days ago" and "1 days ago" read wrong in every language.
  const looksLikeKey =
    days === 0 ? "jobBoard.duplicate.looksLikeToday"
    : days === 1 ? "jobBoard.duplicate.looksLikeYesterday"
    : "jobBoard.duplicate.looksLike";
  return (
    <div
      data-testid="duplicate-prompt"
      className="mb-3 rounded-md border border-warn bg-warn-wash px-3 py-2"
    >
      <p className="text-[12px] text-ink">
        {t(looksLikeKey, { title: data.other.title, days })}{" "}
        {t("jobBoard.duplicate.resolveHint")}
      </p>
      <div className="mt-1.5 flex gap-2">
        <button
          data-testid="duplicate-different"
          disabled={dismiss.isPending}
          onClick={() => dismiss.mutate({ jobId, otherJobId: data.other_job_id })}
          className="rounded-md border border-border-2 bg-surface px-2.5 py-1 text-[12px] text-ink-2 disabled:opacity-40"
        >
          {t("jobBoard.duplicate.different")}
        </button>
      </div>
    </div>
  );
}
