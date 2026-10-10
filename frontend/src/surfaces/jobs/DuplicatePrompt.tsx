// "This looks like the job you added on Monday" (url-less-jobs.md 9.6).
//
// The case: the user pasted an emailed JD on Monday, Wednesday's scan wrote its
// own row for the same posting, and the board shows the role twice. Both rows
// are correct by their own key, so nothing upstream prevents it. The answer is
// to ask once and never again.
//
// It rides on the card the user did NOT create, because that is the one that
// appeared without being asked for. The merge is never automatic, which is the
// stance `_flag_cross_listings` has always taken: flag, never drop.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useDismissJobDuplicate, useJobDuplicate, useMergeJobDuplicate } from "../../api/queries";
import { daysBetween } from "../jobFormat";
import { Modal } from "../../shell/Modal";

function MergeDialog({
  prompt,
  onClose,
}: {
  prompt: NonNullable<ReturnType<typeof useJobDuplicate>["data"]>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const merge = useMergeJobDuplicate();
  const [refused, setRefused] = useState(prompt.both_have_applications);
  return (
    <Modal title={t("jobBoard.duplicate.mergeTitle")} onClose={onClose} size="md">
      <div className="space-y-3 text-[12.5px] text-ink-2">
        {refused ? (
          // `uq_applications_job` permits 1 application per job, so a merge
          // cannot reparent one. Guessing with the user's pipeline is the worst
          // option available, so this stops and says so.
          <p data-testid="duplicate-merge-refused" className="text-ink">
            {t("jobBoard.duplicate.bothTracked")}
          </p>
        ) : (
          <>
            <div>
              <div className="text-[11.5px] text-ink-3">
                {t("jobBoard.duplicate.keeping")}
              </div>
              <p>{t("jobBoard.duplicate.keepingWhat")}</p>
            </div>
            <div>
              <div className="text-[11.5px] text-ink-3">{t("jobBoard.duplicate.taking")}</div>
              <p>{t("jobBoard.duplicate.takingWhat")}</p>
            </div>
            <p className="text-[11.5px] text-ink-3">{t("jobBoard.duplicate.nothingLost")}</p>
          </>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button
            data-testid="duplicate-keep-both"
            onClick={onClose}
            className="rounded-md border border-border-2 px-2.5 py-1 text-[12px] text-ink-2"
          >
            {t("jobBoard.duplicate.keepBoth")}
          </button>
          {refused ? null : (
            <button
              data-testid="duplicate-merge-confirm"
              disabled={merge.isPending}
              onClick={() =>
                merge.mutate(
                  { jobId: prompt.other_job_id, otherJobId: prompt.job_id },
                  {
                    onSuccess: (r) => {
                      // The server is the authority on the refusal: both rows
                      // may have gained an application since this opened.
                      if (r.status === "merge_refused") setRefused(true);
                      else onClose();
                    },
                  },
                )
              }
              className="rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-white disabled:opacity-40"
            >
              {t("jobBoard.duplicate.merge")}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}

export function DuplicatePrompt({ jobId }: { jobId: string }) {
  const { t } = useTranslation();
  const { data } = useJobDuplicate(jobId);
  const dismiss = useDismissJobDuplicate();
  const [comparing, setComparing] = useState(false);
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
        })}
      </p>
      <div className="mt-1.5 flex gap-2">
        <button
          data-testid="duplicate-compare"
          onClick={() => setComparing(true)}
          className="rounded-md border border-border-2 bg-surface px-2.5 py-1 text-[12px] text-ink-2"
        >
          {t("jobBoard.duplicate.compare")}
        </button>
        <button
          data-testid="duplicate-different"
          disabled={dismiss.isPending}
          onClick={() => dismiss.mutate({ jobId, otherJobId: data.other_job_id })}
          className="rounded-md border border-border-2 bg-surface px-2.5 py-1 text-[12px] text-ink-2 disabled:opacity-40"
        >
          {t("jobBoard.duplicate.different")}
        </button>
      </div>
      {comparing ? <MergeDialog prompt={data} onClose={() => setComparing(false)} /> : null}
    </div>
  );
}
