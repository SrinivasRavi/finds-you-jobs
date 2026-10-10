// The one job-fields block both Add-a-job doors render (US-JB-07 and the
// Tracker's manual add — docs/internal/url-less-jobs.md sections 9.1 and 9.2):
// the link box with its Fetch retry, the 1-line autofill status, and the
// fields. The link is labelled recommended and carries no `required`, so
// title plus description is the whole save gate (section 5.1).

import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useJobPreview } from "../../api/queries";
import { JobTombstonedError, type JobDraft } from "../../api/types";

export type AutofillState =
  | { kind: "idle" }
  | { kind: "reading" }
  | { kind: "filled"; count: number; adapter: string }
  | { kind: "failed"; reason: string }
  | { kind: "tombstoned" };

const FIELD_CLS =
  "rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none";

/** The fields autofill may fill — everything but the link itself, which the
 *  user typed and we never overwrite. */
const FILLABLE = ["title", "company", "location", "description", "salary"] as const;

export function emptyDraft(): JobDraft {
  return {
    canonical_url: "",
    title: "",
    company: "",
    location: "",
    description: "",
    salary: "",
    source_adapter: "paste-text",
  };
}

/** D10 fires this on blur, with the Fetch button as the retry. D11 fills the
 *  empty fields only, counts what it touched, and keeps the pre-fetch values
 *  so Undo restores exactly those. */
export function useAutofill(draft: JobDraft, setDraft: (next: JobDraft) => void) {
  const [state, setState] = useState<AutofillState>({ kind: "idle" });
  const before = useRef<Partial<JobDraft> | null>(null);
  const lastRun = useRef("");
  // The fields stay editable while a fetch is in flight, so its result merges
  // into the draft as it is when the fetch lands, not as it was on blur.
  const latest = useRef(draft);
  latest.current = draft;
  const preview = useJobPreview();

  function run(url: string, { force = false }: { force?: boolean } = {}) {
    const link = url.trim();
    if (!link || !/^https?:\/\//i.test(link)) return;
    // Blur fires every time focus leaves the box, including on the way to
    // Cancel. Re-probing a link we already read would spend another fetch and
    // stamp over an Undo the user may still want.
    if (!force && link === lastRun.current) return;
    lastRun.current = link;
    setState({ kind: "reading" });
    preview.mutate(link, {
      onSuccess: (fetched) => {
        const current = latest.current;
        // The link changed while this one was being read; its result is stale.
        if (current.canonical_url.trim() !== link) {
          setState({ kind: "idle" });
          return;
        }
        const touched: Partial<JobDraft> = {
          canonical_url: current.canonical_url,
          source_adapter: current.source_adapter,
        };
        const next = { ...current, canonical_url: fetched.canonical_url || link };
        let count = 0;
        for (const key of FILLABLE) {
          if (!next[key] && fetched[key]) {
            touched[key] = current[key];
            next[key] = fetched[key];
            count += 1;
          }
        }
        before.current = touched;
        next.source_adapter = fetched.source_adapter || "paste-url";
        setDraft(next);
        setState({ kind: "filled", count, adapter: fetched.source_adapter || "the page" });
      },
      onError: (err) => {
        if (err instanceof JobTombstonedError) {
          setState({ kind: "tombstoned" });
          return;
        }
        // The sidecar's own reason, verbatim — it names the real problem
        // (a JS-only page, a dead URL) better than anything generic here.
        setState({ kind: "failed", reason: err instanceof Error ? err.message : String(err) });
      },
    });
  }

  function undo() {
    if (before.current) setDraft({ ...latest.current, ...before.current });
    before.current = null;
    setState({ kind: "idle" });
  }

  return {
    state,
    run,
    undo,
    reset: () => {
      lastRun.current = "";
      setState({ kind: "idle" });
    },
  };
}

function StatusLine({
  state,
  onRetry,
  onUndo,
}: {
  state: AutofillState;
  onRetry: () => void;
  onUndo: () => void;
}) {
  const { t } = useTranslation();
  if (state.kind === "idle") {
    return (
      <p className="text-[11.5px] leading-snug text-ink-4">↳ {t("jobBoard.addModal.hint")}</p>
    );
  }
  if (state.kind === "reading") {
    return (
      <p className="flex items-center gap-1.5 text-[11.5px] text-ink-3" data-testid="autofill-reading">
        <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
        {t("jobBoard.addModal.reading")}
      </p>
    );
  }
  if (state.kind === "filled") {
    return (
      <p className="text-[11.5px] text-good" data-testid="autofill-filled">
        ✓ {t("jobBoard.addModal.filled", { count: state.count, adapter: state.adapter })}{" "}
        <button type="button" onClick={onUndo} className="text-accent hover:underline">
          {t("jobBoard.addModal.undo")}
        </button>
      </p>
    );
  }
  if (state.kind === "failed") {
    return (
      <p
        className="rounded-md border border-warn/30 bg-warn/5 px-2.5 py-1.5 text-[11.5px] leading-snug text-warn"
        data-testid="autofill-failed"
      >
        ⚠ {t("jobBoard.addModal.failed", { reason: state.reason })}{" "}
        <button type="button" onClick={onRetry} className="underline hover:no-underline">
          {t("jobBoard.addModal.tryAgain")}
        </button>
      </p>
    );
  }
  return (
    <p
      className="rounded-md border border-bad/40 bg-bad-wash px-2.5 py-1.5 text-[11.5px] leading-snug text-bad"
      data-testid="add-job-tombstoned"
    >
      ⛔ {t("jobBoard.addModal.tombstoned")}
    </p>
  );
}

export function JobFields({
  draft,
  setDraft,
  autofill,
  showSalary = true,
}: {
  draft: JobDraft;
  setDraft: (next: JobDraft) => void;
  autofill: ReturnType<typeof useAutofill>;
  showSalary?: boolean;
}) {
  const { t } = useTranslation();
  const patch = (fields: Partial<JobDraft>) => setDraft({ ...draft, ...fields });
  // A JD pasted into the link box: anything that isn't http(s) can never be a
  // canonical URL, so the sniff costs nothing (section 9.1).
  const link = draft.canonical_url;
  const looksLikeJd = link.length > 80 && !/^https?:\/\//i.test(link.trim());
  const fields: [keyof JobDraft, string, boolean][] = [
    ["title", t("jobBoard.addModal.fieldTitle"), true],
    ["company", t("jobBoard.addModal.fieldCompany"), false],
    ["location", t("jobBoard.addModal.fieldLocation"), false],
  ];
  // Pre-existing defect, fixed here rather than in the form this replaces:
  // JobDraft has always carried salary and the form never rendered it, so a
  // hand-added job lost it on save (section 9.1 item 4).
  if (showSalary) fields.push(["salary", t("jobBoard.addModal.fieldSalary"), false]);

  return (
    <>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between">
          <label className="text-[12.5px] text-ink-2" htmlFor="add-job-link">
            {t("jobBoard.addModal.linkLabel")}
          </label>
          <span className="text-[11px] text-ink-4">{t("jobBoard.addModal.linkTag")}</span>
        </div>
        <div className="flex gap-2">
          <input
            id="add-job-link"
            type="url"
            autoFocus
            data-testid="add-job-url-input"
            value={link}
            onChange={(e) => {
              patch({ canonical_url: e.target.value });
              if (autofill.state.kind === "tombstoned") autofill.reset();
            }}
            onBlur={() => autofill.run(link)}
            placeholder="https://company.com/careers/senior-engineer"
            className={"min-w-0 flex-1 " + FIELD_CLS}
          />
          <button
            type="button"
            data-testid="add-job-fetch-btn"
            onClick={() => autofill.run(link, { force: true })}
            className="shrink-0 rounded-md border border-border-2 bg-surface px-3 text-[12.5px] text-ink-2 hover:bg-surface-3"
          >
            {t("jobBoard.addModal.fetch")}
          </button>
        </div>
        <StatusLine
          state={autofill.state}
          onRetry={() => autofill.run(link, { force: true })}
          onUndo={autofill.undo}
        />
        {looksLikeJd ? (
          <p className="text-[11.5px] text-ink-3" data-testid="add-job-jd-sniff">
            {t("jobBoard.addModal.looksLikeJd")}{" "}
            <button
              type="button"
              onClick={() =>
                setDraft({ ...draft, canonical_url: "", description: draft.description || link })
              }
              className="text-accent hover:underline"
            >
              {t("jobBoard.addModal.moveIt")}
            </button>
          </p>
        ) : null}
      </div>

      {fields.map(([key, label, required]) => (
        <label key={key} className="flex items-center gap-3">
          <span className="w-[78px] shrink-0 text-[12.5px] text-ink-2">
            {label}
            {required ? <span className="text-bad"> *</span> : null}
          </span>
          <input
            value={draft[key]}
            onChange={(e) => patch({ [key]: e.target.value })}
            data-testid={`add-job-${key}`}
            className={"min-w-0 flex-1 " + FIELD_CLS}
          />
        </label>
      ))}

      <div className="flex flex-col gap-1.5">
        <span className="text-[12.5px] text-ink-2">
          {t("jobBoard.addModal.fieldDescription")}
          <span className="text-bad"> *</span>
        </span>
        <textarea
          value={draft.description}
          onChange={(e) => patch({ description: e.target.value })}
          rows={5}
          data-testid="add-job-description"
          className={"resize-y " + FIELD_CLS}
        />
      </div>
    </>
  );
}

/** What either door sends. Only an http(s) string reaches the link, and the
 *  sidecar re-checks (section 9.1 item 5). A link whose autofill never landed
 *  is still a pasted URL, so it can't keep the `paste-text` it started with. */
export function draftForSave(draft: JobDraft): JobDraft {
  const link = draft.canonical_url.trim();
  if (!/^https?:\/\//i.test(link)) {
    return { ...draft, canonical_url: "", source_adapter: "paste-text" };
  }
  const adapter = draft.source_adapter === "paste-text" ? "" : draft.source_adapter;
  return { ...draft, canonical_url: link, source_adapter: adapter || "paste-url" };
}

/** Title and description are what `compose_job_text` renders, so they're the
 *  save gate (section 9.1 item 3). A tombstoned link blocks outright. */
export function canSave(draft: JobDraft, state: AutofillState): boolean {
  return (
    state.kind !== "tombstoned" &&
    draft.title.trim().length > 0 &&
    draft.description.trim().length > 0
  );
}
