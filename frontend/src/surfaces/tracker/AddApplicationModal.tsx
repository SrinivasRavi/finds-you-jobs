// "Add a job application" (FR-TR manual-add) — the Tracker sibling of the Job
// Board's Add-by-URL. Same paste→preview→edit flow, plus the pipeline stage and
// the optional resume/cover the user submitted (stored content-addressed).
// (Extracted from Tracker.tsx 2026-07-25, F-M6 monolith split — pure move,
// zero behavior change.) Not memoized: it mounts only while open.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAddManualApplication, useJobPreview } from "../../api/queries";
import type { JobDraft, ManualApplicationInput } from "../../api/types";
import { JobTombstonedError } from "../../api/types";
import { Icon } from "../../shell/icons";
import { Modal } from "../../shell/Modal";

// Stages a manually-logged application can land in — it's already been applied
// to, so it starts at Applied (or later); the pre-submission columns don't apply.
const MANUAL_STAGES: ManualApplicationInput["stage"][] = [
  "Applied",
  "Interviewing",
  "Offer",
  "Rejected",
];

// The upload formats the sidecar accepts (mirrors `documents.ALLOWED_TYPES`).
const DOC_ACCEPT = ".pdf,.doc,.docx,.txt,.md,.rtf";

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
          accept={DOC_ACCEPT}
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
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<"entry" | "fetching" | "editing">("entry");
  const [draft, setDraft] = useState<JobDraft | null>(null);
  const [stage, setStage] = useState<ManualApplicationInput["stage"]>("Applied");
  const [notes, setNotes] = useState("");
  const [resume, setResume] = useState<File | null>(null);
  const [cover, setCover] = useState<File | null>(null);
  const [tombstoned, setTombstoned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const preview = useJobPreview();
  const addApp = useAddManualApplication();

  function fetchDetails() {
    setPhase("fetching");
    setTombstoned(false);
    setError(null);
    preview.mutate(url, {
      onSuccess: (d) => {
        setDraft(d);
        setPhase("editing");
      },
      onError: (err) => {
        if (err instanceof JobTombstonedError) {
          setTombstoned(true);
          setPhase("entry");
          return;
        }
        // Other fetch failures: still let the user fill fields by hand
        // (rank-don't-gate escape hatch — they already applied, so we never
        // block logging it).
        setDraft({
          canonical_url: url,
          title: "",
          company: "",
          location: "",
          description: "",
          salary: "",
          source_adapter: "paste-url",
        });
        setPhase("editing");
      },
    });
  }

  function submit() {
    if (!draft) return;
    setError(null);
    addApp.mutate(
      {
        canonical_url: draft.canonical_url,
        title: draft.title,
        company: draft.company,
        location: draft.location,
        description: draft.description,
        salary: draft.salary,
        source_adapter: draft.source_adapter || "paste-url",
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

  function patch(fields: Partial<JobDraft>) {
    setDraft((d) => (d ? { ...d, ...fields } : d));
  }

  return (
    <Modal title={t("tracker.addApplication")} onClose={onClose} width={520}>
      {phase === "entry" ? (
        <form
          className="flex flex-col gap-3 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            fetchDetails();
          }}
        >
          <label className="text-[12.5px] text-ink-2">
            {t("tracker.addApp.intro")}
          </label>
          {tombstoned ? (
            <p
              data-testid="add-app-tombstoned"
              className="rounded-md border border-bad/40 bg-bad-wash px-3 py-2 text-[12px] text-bad"
            >
              {t("tracker.addApp.tombstoned")}
            </p>
          ) : null}
          <input
            type="url"
            required
            autoFocus
            data-testid="add-app-url-input"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setTombstoned(false);
            }}
            placeholder="https://company.com/careers/senior-engineer"
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
          />
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
              data-testid="add-app-fetch-btn"
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
            >
              {t("tracker.addApp.fetch")}
            </button>
          </div>
        </form>
      ) : phase === "fetching" ? (
        <div className="grid place-items-center px-5 py-10 text-[13px] text-ink-3">
          <div className="flex items-center gap-2">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
            {t("tracker.addApp.fetching")}
          </div>
        </div>
      ) : (
        <form
          className="flex flex-col gap-3 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="text-[11.5px] text-ink-3">
            {url || t("tracker.addApp.noUrl")}{" "}
            <button
              type="button"
              onClick={() => setPhase("entry")}
              className="text-accent hover:underline"
            >
              {t("tracker.addApp.refetch")}
            </button>
          </div>
          <input
            value={draft?.title ?? ""}
            onChange={(e) => patch({ title: e.target.value })}
            placeholder={t("tracker.addApp.titlePlaceholder")}
            data-testid="add-app-title"
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <input
            value={draft?.company ?? ""}
            onChange={(e) => patch({ company: e.target.value })}
            placeholder={t("tracker.addApp.companyPlaceholder")}
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <input
            value={draft?.location ?? ""}
            onChange={(e) => patch({ location: e.target.value })}
            placeholder={t("tracker.addApp.locationPlaceholder")}
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <label className="flex items-center justify-between gap-3 text-[12.5px] text-ink-2">
            {t("tracker.addApp.stage")}
            <select
              value={stage}
              onChange={(e) => setStage(e.target.value as ManualApplicationInput["stage"])}
              data-testid="add-app-stage"
              className="rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink"
            >
              {MANUAL_STAGES.map((s) => (
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
              disabled={addApp.isPending}
              data-testid="add-app-submit-btn"
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:opacity-60"
            >
              {addApp.isPending ? t("tracker.addApp.adding") : t("tracker.addApp.submit")}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
