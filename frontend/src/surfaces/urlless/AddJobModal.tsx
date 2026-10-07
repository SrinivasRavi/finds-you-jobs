// Drawings 1 and 2 (sections 9.1 and 9.1.1): "Add a job by URL" becomes "Add a
// job". 1 form, every field visible from the moment it opens, the link first
// rather than as the gate, and a Score-it-first strip that saves nothing.

import { useState } from "react";

import { Modal } from "../../shell/Modal";
import { canSave, JobFields, useAutofill } from "./JobFields";
import { COPY, EMPTY_DRAFT, scorePreview, type PreviewDraft, type TriageScore } from "./mock";

function TriageStrip({
  score,
  busy,
  onAgain,
}: {
  score: TriageScore | null;
  busy: boolean;
  onAgain: () => void;
}) {
  if (busy && !score) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-4 py-3 text-[12.5px] text-ink-3">
        <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
        {COPY.addJob.scoring}
      </div>
    );
  }
  if (!score) return null;
  return (
    <div
      className="rounded-lg border border-border bg-surface-2 px-4 py-3"
      data-testid="urlless-triage-strip"
    >
      <div className="flex items-baseline gap-2">
        <span className="text-[20px] font-semibold text-accent">{score.score}</span>
        <span className="text-[12.5px] font-medium text-ink">{score.band}</span>
      </div>
      <div className="mt-0.5 text-[11.5px] text-ink-3">{COPY.addJob.scoredWith(score.method)}</div>
      <ul className="mt-2.5 space-y-1.5 text-[12.5px] text-ink-2">
        {score.reasons.map((r) => (
          <li key={r} className="flex gap-1.5">
            <span className="mt-1 size-1 shrink-0 rounded-full bg-accent" aria-hidden="true" />
            <span>{r}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex items-center gap-3 border-t border-border pt-2.5 text-[11.5px] text-ink-3">
        {COPY.addJob.nothingSaved}
        <button type="button" onClick={onAgain} className="text-accent hover:underline">
          {COPY.addJob.scoreAgain}
        </button>
      </div>
    </div>
  );
}

export function AddJobModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (draft: PreviewDraft) => void;
}) {
  const [draft, setDraft] = useState<PreviewDraft>(EMPTY_DRAFT);
  const autofill = useAutofill(draft, setDraft);
  const [score, setScore] = useState<TriageScore | null>(null);
  const [scoring, setScoring] = useState(false);
  const valid = canSave(draft, autofill.state);

  function triage() {
    setScoring(true);
    setScore(null);
    void scorePreview(draft.description).then((s) => {
      setScore(s);
      setScoring(false);
    });
  }

  return (
    <Modal title={COPY.addJob.title} onClose={onClose} width={520}>
      <form
        className="flex flex-col gap-3 px-5 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) onSaved(draft);
        }}
      >
        <JobFields draft={draft} setDraft={setDraft} autofill={autofill} />
        <TriageStrip score={score} busy={scoring} onAgain={triage} />
        <div className="mt-1 flex items-center gap-2">
          <button
            type="button"
            data-testid="urlless-score-first"
            disabled={!draft.description.trim()}
            onClick={triage}
            className="rounded-full border border-border-2 bg-surface px-2.5 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3 disabled:opacity-50"
          >
            {COPY.addJob.scoreFirst}
          </button>
          <div className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
          >
            {COPY.addJob.cancel}
          </button>
          <button
            type="submit"
            disabled={!valid}
            data-testid="urlless-add-submit"
            title={valid ? undefined : COPY.addJob.needTitle}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {COPY.addJob.submit}
          </button>
        </div>
      </form>
    </Modal>
  );
}
