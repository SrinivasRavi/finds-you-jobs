// "Add a job by URL" became "Add a job" (US-JB-07, url-less-jobs.md section
// 9.1): 1 form, every field visible from the moment it opens, the link first
// rather than as the gate, and a Score-it-first strip that saves nothing.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useTriageScore, type TriageResult } from "../../api/queries";
import type { JobDraft } from "../../api/types";
import { Modal } from "../../shell/Modal";
import { canSave, emptyDraft, JobFields, useAutofill } from "./JobFields";

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
        {score.reasons.map((r) => (
          <li key={r} className="flex gap-1.5">
            <span className="mt-1 size-1 shrink-0 rounded-full bg-accent" aria-hidden="true" />
            <span>{r}</span>
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
  const [score, setScore] = useState<TriageResult | null>(null);
  const [triageError, setTriageError] = useState<string | null>(null);
  const valid = canSave(draft, autofill.state);

  function runTriage() {
    setScore(null);
    setTriageError(null);
    triage.mutate(draft.description, {
      onSuccess: setScore,
      onError: (e) => setTriageError(e instanceof Error ? e.message : String(e)),
    });
  }

  function submit() {
    // The link box holds whatever the user typed; only an http(s) string ever
    // reaches the draft's URL, and the sidecar re-checks (section 9.1 item 5).
    const link = draft.canonical_url.trim();
    const isUrl = /^https?:\/\//i.test(link);
    void onAdd({
      ...draft,
      canonical_url: isUrl ? link : "",
      source_adapter: isUrl ? draft.source_adapter || "paste-url" : "paste-text",
    }).then(() => onClose());
  }

  return (
    <Modal title={t("jobBoard.addModal.title")} onClose={onClose} width={520}>
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
