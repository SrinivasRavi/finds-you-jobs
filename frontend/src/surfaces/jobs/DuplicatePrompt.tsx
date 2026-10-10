// "This looks like the job you added on Monday" (url-less-jobs.md 9.6).
//
// The case: the user pasted an emailed JD on Monday, Wednesday's scan wrote its
// own row for the same posting, and the board shows the role twice. The app
// says so and stops there: the user deletes the one they don't need, and the
// app never merges or picks a survivor (maintainer, url-less-jobs.md line 409).
// "They're different" silences the notice for that pair, from either card.

import { useTranslation } from "react-i18next";

import { useDismissJobDuplicate, useJobDuplicate } from "../../api/queries";
import { daysBetween } from "../jobFormat";

export function DuplicatePrompt({ jobId }: { jobId: string }) {
  const { t } = useTranslation();
  const { data } = useJobDuplicate(jobId);
  const dismiss = useDismissJobDuplicate();
  if (!data) return null;
  const days = daysBetween(data.other_added_at, "round");
  return (
    <div
      data-testid="duplicate-prompt"
      className="mb-3 rounded-md border border-warn bg-warn-wash px-3 py-2"
    >
      <p className="text-[12px] text-ink">
        {t("jobBoard.duplicate.looksLike", {
          title: data.other.title,
          days: Number.isFinite(days) ? days : 0,
        })}{" "}
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
