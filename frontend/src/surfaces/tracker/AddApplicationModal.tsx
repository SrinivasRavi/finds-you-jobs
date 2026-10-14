// "Add a job application" (FR-TR manual-add), the Tracker's Add a job: 1 form
// with the link recommended, not required, plus the stage and the resume or cover
// actually submitted. Someone logging a 3-week-old application rarely has the URL
// (url-less-jobs.md section 9.2). Not memoized: it mounts only while open.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAddManualApplication } from "../../api/queries";
import type { JobDraft, ManualApplicationInput } from "../../api/types";
// It's already been applied to, so it starts at Applied or later; the
// pre-submission columns don't apply.
import { POST_APPLICATION_STAGES } from "../../api/types";
import { Icon } from "../../shell/icons";
import { Modal } from "../../shell/Modal";
import { ATTACH_ACCEPT } from "../../shell/uploadFormats";
import { canSave, draftForSave, emptyDraft, JobFields, useAutofill } from "../jobs/JobFields";

function FilePicker({
  label,
  file,
  onPick,
  testid,
}: {
  label: string;
  file: File | null;
  onPick: (f: File | null) => void;
  testid: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2">
      <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] text-ink-2 hover:border-accent">
        <Icon name="file" size={13} strokeWidth={2} />
        {file ? t("tracker.documents.change") : label}
        <input
          type="file"
          accept={ATTACH_ACCEPT}
          data-testid={testid}
          className="hidden"
          onChange={(e) => onPick(e.target.files?.[0] ?? null)}
        />
      </label>
      {file ? (
        <span className="inline-flex min-w-0 items-center gap-1 text-[12px] text-ink-3">
          <span className="max-w-[180px] truncate">{file.name}</span>
          <button
            type="button"
            onClick={() => onPick(null)}
            className="text-ink-4 hover:text-bad"
            aria-label={t("tracker.documents.remove", { label })}
          >
            ×
          </button>
        </span>
      ) : (
        <span className="text-[11.5px] text-ink-4">{t("tracker.documents.optional")}</span>
      )}
    </div>
  );
}

export function AddApplicationModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<JobDraft>(emptyDraft);
  const [stage, setStage] = useState<ManualApplicationInput["stage"]>("Applied");
  const [notes, setNotes] = useState("");
  const [resume, setResume] = useState<File | null>(null);
  const [cover, setCover] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const autofill = useAutofill(draft, setDraft);
  const addApp = useAddManualApplication();
  const valid = canSave(draft, autofill.state);

  function submit() {
    setError(null);
    const saved = draftForSave(draft);
    addApp.mutate(
      {
        canonical_url: saved.canonical_url,
        title: saved.title,
        company: saved.company,
        location: saved.location,
        description: saved.description,
        salary: saved.salary,
        source_adapter: saved.source_adapter,
        stage,
        notes,
        resume,
        cover,
      },
      {
        onSuccess: () => onClose(),
        onError: (err) => setError(err instanceof Error ? err.message : String(err)),
      },
    );
  }

  return (
    <Modal title={t("tracker.addApplication")} onClose={onClose} size="md">
      <form
        className="flex flex-col gap-3 px-5 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) submit();
        }}
      >
        <p className="text-[12.5px] text-ink-2">{t("tracker.addApp.intro")}</p>
        {/* Salary is the board modal's field, not this one's: a card logged
            after the fact is about the application, not the offer. */}
        <JobFields draft={draft} setDraft={setDraft} autofill={autofill} showSalary={false} />
        <label className="flex items-center justify-between gap-3 text-[12.5px] text-ink-2">
          {t("tracker.addApp.stage")}
          <select
            value={stage}
            onChange={(e) => setStage(e.target.value as ManualApplicationInput["stage"])}
            data-testid="add-app-stage"
            className="rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink"
          >
            {POST_APPLICATION_STAGES.map((s) => (
              <option key={s} value={s}>
                {t(`tracker.stage.${s}`)}
              </option>
            ))}
          </select>
        </label>
        <div className="space-y-2 rounded-md border border-border bg-surface-2 px-3 py-2.5">
          <div className="text-[12px] font-medium text-ink-2">
            {t("tracker.documents.used")}{" "}
            <span className="font-normal text-ink-4">{t("tracker.documents.optionalTag")}</span>
          </div>
          <FilePicker label={t("tracker.documents.attachResume")} file={resume} onPick={setResume} testid="add-app-resume" />
          <FilePicker label={t("tracker.documents.attachCover")} file={cover} onPick={setCover} testid="add-app-cover" />
        </div>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder={t("tracker.addApp.notesPlaceholder")}
          rows={3}
          className="resize-y rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
        />
        {error ? (
          <p data-testid="add-app-error" className="text-[12px] text-bad">
            {error}
          </p>
        ) : null}
        <div className="mt-1 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
          >
            {t("tracker.cancel")}
          </button>
          <button
            type="submit"
            disabled={!valid || addApp.isPending}
            data-testid="add-app-submit-btn"
            title={valid ? undefined : t("jobBoard.addModal.needTitle")}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {addApp.isPending ? t("tracker.addApp.adding") : t("tracker.addApp.submit")}
          </button>
        </div>
      </form>
    </Modal>
  );
}
