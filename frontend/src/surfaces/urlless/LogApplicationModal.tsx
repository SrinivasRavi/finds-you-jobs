// Drawing 3 (section 9.2): the Tracker's "Add a job application" gets the same
// link box, the same autofill and the same description requirement, and keeps
// its own extras below them (stage, the documents actually sent, notes). The
// person logging a 3-week-old application from an email is the one least likely
// to still have the URL, so the gate goes here first.

import { useState } from "react";

import { Icon } from "../../shell/icons";
import { Modal } from "../../shell/Modal";
import { canSave, JobFields, useAutofill } from "./JobFields";
import { COPY, EMPTY_DRAFT, type PreviewDraft } from "./mock";

const MANUAL_STAGES = ["Applied", "Interviewing", "Offer", "Rejected"];

function FilePicker({ label, name, onPick }: { label: string; name: string | null; onPick: (n: string | null) => void }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-[80px] shrink-0 text-[12px] text-ink-2">{label}</span>
      <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] text-ink-2 hover:border-accent">
        <Icon name="file" size={13} strokeWidth={2} />
        {COPY.logApp.chooseFile}
        <input
          type="file"
          className="hidden"
          onChange={(e) => onPick(e.target.files?.[0]?.name ?? null)}
        />
      </label>
      {name ? (
        <span className="inline-flex min-w-0 items-center gap-1 text-[12px] text-ink-3">
          <span className="max-w-[160px] truncate">{name}</span>
          <button type="button" onClick={() => onPick(null)} className="text-ink-4 hover:text-bad">
            ×
          </button>
        </span>
      ) : (
        <span className="text-[11.5px] text-ink-4">{COPY.logApp.noneChosen}</span>
      )}
    </div>
  );
}

export function LogApplicationModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (draft: PreviewDraft, stage: string) => void;
}) {
  const [draft, setDraft] = useState<PreviewDraft>(EMPTY_DRAFT);
  const autofill = useAutofill(draft, setDraft);
  const [stage, setStage] = useState(MANUAL_STAGES[0]!);
  const [notes, setNotes] = useState("");
  const [resume, setResume] = useState<string | null>(null);
  const [cover, setCover] = useState<string | null>(null);
  const valid = canSave(draft, autofill.state);

  return (
    <Modal title={COPY.logApp.title} onClose={onClose} width={520}>
      <form
        className="flex flex-col gap-3 px-5 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) onSaved(draft, stage);
        }}
      >
        <p className="text-[12.5px] text-ink-2">{COPY.logApp.intro}</p>
        <JobFields draft={draft} setDraft={setDraft} autofill={autofill} showSalary={false} />
        <label className="flex items-center justify-between gap-3 text-[12.5px] text-ink-2">
          {COPY.logApp.stage}
          <select
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            data-testid="urlless-log-stage"
            className="rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink"
          >
            {MANUAL_STAGES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <div className="space-y-2 rounded-md border border-border bg-surface-2 px-3 py-2.5">
          <div className="text-[12px] font-medium text-ink-2">
            {COPY.logApp.documents}{" "}
            <span className="font-normal text-ink-4">{COPY.logApp.optional}</span>
          </div>
          <FilePicker label={COPY.logApp.resume} name={resume} onPick={setResume} />
          <FilePicker label={COPY.logApp.cover} name={cover} onPick={setCover} />
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-[12.5px] text-ink-2">{COPY.logApp.notes}</span>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            className="resize-y rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
        </div>
        <div className="mt-1 flex justify-end gap-2">
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
            data-testid="urlless-log-submit"
            title={valid ? undefined : COPY.addJob.needTitle}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {COPY.logApp.submit}
          </button>
        </div>
      </form>
    </Modal>
  );
}
