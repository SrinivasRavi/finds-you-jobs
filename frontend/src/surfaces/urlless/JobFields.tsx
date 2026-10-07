// The one job-fields block both Add-a-job doors render (drawings 1 and 3,
// docs/internal/url-less-jobs.md sections 9.1 and 9.2): the link box with its
// Fetch retry, the 1-line autofill status, and the 5 fields. The link is
// labelled recommended and carries no `required`, so title plus description is
// the whole gate (section 5.1).

import { useRef, useState } from "react";

import { COPY, probePreview, type PreviewDraft } from "./mock";

export type AutofillState =
  | { kind: "idle" }
  | { kind: "reading" }
  | { kind: "filled"; count: number; adapter: string }
  | { kind: "failed"; reason: string }
  | { kind: "tombstoned" };

const FIELD_CLS =
  "rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none";

/** D11: fill the empty fields only, count what was touched, and keep the
 *  pre-fetch values so Undo restores exactly those. */
export function useAutofill(
  draft: PreviewDraft,
  setDraft: (next: PreviewDraft) => void,
) {
  const [state, setState] = useState<AutofillState>({ kind: "idle" });
  const before = useRef<PreviewDraft | null>(null);

  function run(url: string) {
    const link = url.trim();
    if (!link) return;
    setState({ kind: "reading" });
    void probePreview(link).then((result) => {
      if (result.kind === "tombstoned") {
        setState({ kind: "tombstoned" });
        return;
      }
      if (result.kind === "failed") {
        setState({ kind: "failed", reason: result.reason });
        return;
      }
      before.current = draft;
      const next = { ...draft };
      let count = 0;
      for (const [key, value] of Object.entries(result.fields)) {
        const k = key as keyof PreviewDraft;
        if (!next[k] && value) {
          next[k] = value;
          count += 1;
        }
      }
      setDraft(next);
      setState({ kind: "filled", count, adapter: result.adapter });
    });
  }

  function undo() {
    if (before.current) setDraft(before.current);
    before.current = null;
    setState({ kind: "idle" });
  }

  return { state, run, undo, reset: () => setState({ kind: "idle" }) };
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
  if (state.kind === "idle") {
    return <p className="text-[11.5px] leading-snug text-ink-4">↳ {COPY.addJob.hint}</p>;
  }
  if (state.kind === "reading") {
    return (
      <p
        className="flex items-center gap-1.5 text-[11.5px] text-ink-3"
        data-testid="autofill-reading"
      >
        <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
        {COPY.addJob.reading}
      </p>
    );
  }
  if (state.kind === "filled") {
    return (
      <p className="text-[11.5px] text-good" data-testid="autofill-filled">
        ✓ {COPY.addJob.filled(state.count, state.adapter)}{" "}
        <button type="button" onClick={onUndo} className="text-accent hover:underline">
          {COPY.addJob.undo}
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
        ⚠ {COPY.addJob.failed(state.reason)}{" "}
        <button type="button" onClick={onRetry} className="underline hover:no-underline">
          {COPY.addJob.tryAgain}
        </button>
      </p>
    );
  }
  return (
    <p
      className="rounded-md border border-bad/40 bg-bad-wash px-2.5 py-1.5 text-[11.5px] leading-snug text-bad"
      data-testid="autofill-tombstoned"
    >
      ⛔ {COPY.addJob.tombstoned}
    </p>
  );
}

export function JobFields({
  draft,
  setDraft,
  autofill,
  showSalary = true,
}: {
  draft: PreviewDraft;
  setDraft: (next: PreviewDraft) => void;
  autofill: ReturnType<typeof useAutofill>;
  showSalary?: boolean;
}) {
  const patch = (fields: Partial<PreviewDraft>) => setDraft({ ...draft, ...fields });
  // A JD pasted into the link box: `canonicalize_url` returns "" for anything
  // that isn't http(s), so the sniff costs nothing (section 9.1).
  const looksLikeJd = draft.link.length > 80 && !/^https?:\/\//i.test(draft.link.trim());
  return (
    <>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between">
          <label className="text-[12.5px] text-ink-2" htmlFor="urlless-link">
            {COPY.addJob.linkLabel}
          </label>
          <span className="text-[11px] text-ink-4">{COPY.addJob.linkTag}</span>
        </div>
        <div className="flex gap-2">
          <input
            id="urlless-link"
            type="url"
            autoFocus
            data-testid="urlless-link-input"
            value={draft.link}
            onChange={(e) => patch({ link: e.target.value })}
            onBlur={() => autofill.run(draft.link)}
            placeholder="https://company.com/careers/senior-engineer"
            className={"min-w-0 flex-1 " + FIELD_CLS}
          />
          <button
            type="button"
            data-testid="urlless-fetch-btn"
            onClick={() => autofill.run(draft.link)}
            className="shrink-0 rounded-md border border-border-2 bg-surface px-3 text-[12.5px] text-ink-2 hover:bg-surface-3"
          >
            {COPY.addJob.fetch}
          </button>
        </div>
        <StatusLine
          state={autofill.state}
          onRetry={() => autofill.run(draft.link)}
          onUndo={autofill.undo}
        />
        {looksLikeJd ? (
          <p className="text-[11.5px] text-ink-3" data-testid="urlless-jd-sniff">
            {COPY.addJob.looksLikeJd}{" "}
            <button
              type="button"
              onClick={() =>
                setDraft({ ...draft, link: "", description: draft.description || draft.link })
              }
              className="text-accent hover:underline"
            >
              {COPY.addJob.moveIt}
            </button>
          </p>
        ) : null}
      </div>

      {(
        [
          ["title", COPY.addJob.title_, true],
          ["company", COPY.addJob.company, false],
          ["location", COPY.addJob.location, false],
          ...(showSalary ? ([["salary", COPY.addJob.salary, false]] as const) : []),
        ] as [keyof PreviewDraft, string, boolean][]
      ).map(([key, label, required]) => (
        <label key={key} className="flex items-center gap-3">
          <span className="w-[78px] shrink-0 text-[12.5px] text-ink-2">
            {label}
            {required ? <span className="text-bad"> *</span> : null}
          </span>
          <input
            value={draft[key]}
            onChange={(e) => patch({ [key]: e.target.value })}
            data-testid={`urlless-${key}`}
            className={"min-w-0 flex-1 " + FIELD_CLS}
          />
        </label>
      ))}

      <div className="flex flex-col gap-1.5">
        <span className="text-[12.5px] text-ink-2">
          {COPY.addJob.description}
          <span className="text-bad"> *</span>
        </span>
        <textarea
          value={draft.description}
          onChange={(e) => patch({ description: e.target.value })}
          rows={5}
          data-testid="urlless-description"
          className={"resize-y " + FIELD_CLS}
        />
      </div>
    </>
  );
}

/** Title and description are what `compose_job_text` renders, so they're the
 *  save gate (section 9.1 item 3). A tombstoned link blocks outright. */
export function canSave(draft: PreviewDraft, state: AutofillState): boolean {
  return (
    state.kind !== "tombstoned" &&
    draft.title.trim().length > 0 &&
    draft.description.trim().length > 0
  );
}
