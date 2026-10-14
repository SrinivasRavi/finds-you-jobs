// "Add a job by URL" became "Add a job posting" (US-JB-07, url-less-jobs.md
// 9.1): 1 form, every field visible from the moment it opens, the link first
// rather than as the gate, and a Score-it-first strip that saves nothing.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  RunStoppedError,
  useCancelOperation,
  useTriageScore,
  type TriageResult,
} from "../../api/queries";
import { JobTombstonedError, type JobDraft } from "../../api/types";
import { Modal } from "../../shell/Modal";
import { scoreText } from "../../shell/scoreText";
import { canSave, draftForSave, emptyDraft, JobFields, useAutofill } from "./JobFields";

function TriageStrip({
  score,
  busy,
  error,
  onAgain,
}: {
  score: TriageResult | null;
  busy: boolean;
  error: string | null;
  onAgain: () => void;
}) {
  const { t } = useTranslation();
  if (busy && !score) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-4 py-3 text-[12.5px] text-ink-3">
        <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
        {t("jobBoard.addModal.scoring")}
      </div>
    );
  }
  if (error !== null) {
    return (
      <p className="rounded-lg border border-warn/30 bg-warn/5 px-4 py-3 text-[12.5px] text-warn">
        {error}
      </p>
    );
  }
  if (!score) return null;
  return (
    <div className="rounded-lg border border-border bg-surface-2 px-4 py-3" data-testid="triage-strip">
      <div className="flex items-baseline gap-2">
        <span className="text-[20px] font-semibold text-accent">{score.score}</span>
      </div>
      {/* Section 8.7: a JD under 200 characters is downgraded to keyword even
          in AI mode, so the strip names the method that actually ran. */}
      <div className="mt-0.5 text-[11.5px] text-ink-3">
        {t("jobBoard.addModal.scoredWith", { method: score.method })}
      </div>
      <ul className="mt-2.5 space-y-1.5 text-[12.5px] text-ink-2">
        {score.reasons.map((r, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="mt-1 size-1 shrink-0 rounded-full bg-accent" aria-hidden="true" />
            <span>{scoreText(r, t)}</span>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-3 border-t border-border pt-2.5 text-[11.5px] text-ink-3 mt-3">
        {t("jobBoard.addModal.nothingSaved")}
        <button type="button" onClick={onAgain} className="text-accent hover:underline">
          {t("jobBoard.addModal.scoreAgain")}
        </button>
      </div>
    </div>
  );
}

export function AddJobModal({
  onClose,
  onAdd,
}: {
  onClose: () => void;
  onAdd: (draft: JobDraft) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<JobDraft>(emptyDraft);
  const autofill = useAutofill(draft, setDraft);
  const triage = useTriageScore();
  const cancel = useCancelOperation();
  const [runningOp, setRunningOp] = useState<string | null>(null);
  const [score, setScore] = useState<TriageResult | null>(null);
  const [triageError, setTriageError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const valid = canSave(draft, autofill.state) && !saving;

  function runTriage() {
    setScore(null);
    setTriageError(null);
    triage.mutate(
      { description: draft.description, onStarted: setRunningOp },
      {
        onSuccess: setScore,
        onError: (e) =>
          setTriageError(
            e instanceof RunStoppedError
              ? t(e.reason === "timeout" ? "run.timedOut" : "run.stopped")
              : e instanceof Error
                ? e.message
                : String(e),
          ),
        onSettled: () => setRunningOp(null),
      },
    );
  }

  function submit() {
    setSaveError(null);
    setSaving(true);
    void onAdd(draftForSave(draft))
      .then(() => onClose())
      .catch((e: unknown) => {
        // A refused save used to reject into nothing, so the button looked
        // dead and the user had no idea why (maintainer, 2026-10-08). The
        // tombstone is the one that actually happens: the description was
        // permanently deleted, and pasting it back is final.
        setSaving(false);
        setSaveError(
          e instanceof JobTombstonedError
            ? t("jobBoard.addModal.tombstoned")
            : e instanceof Error
              ? e.message
              : String(e),
        );
      });
  }

  return (
    <Modal title={t("jobBoard.addModal.title")} onClose={onClose} size="md">
      <form
        className="flex flex-col gap-3 px-5 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) submit();
        }}
      >
        <JobFields draft={draft} setDraft={setDraft} autofill={autofill} />
        <TriageStrip
          score={score}
          busy={triage.isPending}
          error={triageError}
          onAgain={runTriage}
        />
        {saveError ? (
          <p
            data-testid="add-job-save-error"
            role="alert"
            className="rounded-lg border border-warn/30 bg-warn/5 px-4 py-3 text-[12.5px] text-warn"
          >
            {saveError}
          </p>
        ) : null}
        <div className="mt-1 flex items-center gap-2">
          <button
            type="button"
            data-testid="add-job-score-first"
            disabled={!draft.description.trim() || triage.isPending}
            onClick={runTriage}
            className="rounded-full border border-border-2 bg-surface px-2.5 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3 disabled:opacity-50"
          >
            {t("jobBoard.addModal.scoreFirst")}
          </button>
          {triage.isPending && runningOp ? (
            <button
              type="button"
              data-testid="add-job-score-stop"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(runningOp)}
              className="rounded-full border border-border-2 px-2.5 py-1 text-[11.5px] text-ink-2 disabled:opacity-50"
            >
              {t("run.stop")}
            </button>
          ) : null}
          <div className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
          >
            {t("jobBoard.addModal.cancel")}
          </button>
          <button
            type="submit"
            disabled={!valid}
            data-testid="add-job-submit-btn"
            title={valid ? undefined : t("jobBoard.addModal.needTitle")}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t("jobBoard.addModal.submit")}
          </button>
        </div>
      </form>
    </Modal>
  );
}
