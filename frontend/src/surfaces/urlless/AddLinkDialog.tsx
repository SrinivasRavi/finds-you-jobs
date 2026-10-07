// Drawings 7, 8 and 12 (sections 9.5 and 9.8): 1 dialog, 4 call sites. The
// board detail's Add link, the tracker detail's Add link, the Apply prompt, and
// D12's watch-a-careers-page variant. The compound match check (section 5.4)
// lives behind it once, and Attach anyway is always reachable because
// rank-don't-gate applies to a field the same way it applies to a score.

import { useState } from "react";

import { Modal } from "../../shell/Modal";
import { COPY, matchCheck, type MatchResult, type PreviewJob } from "./mock";

export type LinkDialogVariant = "promote" | "apply" | "watch";

const TITLE: Record<LinkDialogVariant, string> = {
  promote: COPY.addLink.title,
  apply: COPY.addLink.applyTitle,
  watch: COPY.addLink.watchTitle,
};

const PRIMARY: Record<LinkDialogVariant, string> = {
  promote: COPY.addLink.attach,
  apply: COPY.addLink.applyAttach,
  watch: COPY.addLink.watchAttach,
};

const HINT: Record<LinkDialogVariant, string> = {
  promote: COPY.addLink.hint,
  apply: COPY.addLink.hint,
  watch: COPY.addLink.watchHint,
};

function SideBySide({ result, job }: { result: Extract<MatchResult, { kind: "mismatch" }>; job: PreviewJob }) {
  const rows: [string, string][] = [
    [result.pasted.title, job.title],
    [result.pasted.company, job.company],
    [result.pasted.location, job.location],
    [result.pasted.similarity, "n/a"],
  ];
  return (
    <div className="grid grid-cols-2 gap-x-5 text-[12.5px]" data-testid="urlless-mismatch-compare">
      <div className="border-b border-border pb-1 text-[11.5px] font-medium text-ink-3">
        {COPY.addLink.youPasted}
      </div>
      <div className="border-b border-border pb-1 text-[11.5px] font-medium text-ink-3">
        {COPY.addLink.cardSays}
      </div>
      {rows.map(([left, right], i) => (
        <div key={i} className="contents">
          <div className={"pt-1.5 " + (i === 0 ? "font-medium text-ink" : "text-ink-2")}>{left}</div>
          <div className={"pt-1.5 " + (i === 0 ? "font-medium text-ink" : "text-ink-2")}>{right}</div>
        </div>
      ))}
    </div>
  );
}

export function AddLinkDialog({
  job,
  variant = "promote",
  onClose,
  onAttached,
}: {
  job: PreviewJob;
  variant?: LinkDialogVariant;
  onClose: () => void;
  onAttached: (note: string) => void;
}) {
  const [url, setUrl] = useState("https://");
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<MatchResult | null>(null);

  function attach() {
    setChecking(true);
    void matchCheck(url, job).then((r) => {
      setChecking(false);
      if (r.kind === "match") {
        onAttached(variant === "apply" ? COPY.addLink.applyStarted : COPY.addLink.attached);
        return;
      }
      setResult(r);
    });
  }

  if (result?.kind === "mismatch") {
    return (
      <Modal title={COPY.addLink.mismatchTitle} onClose={onClose} width={520}>
        <div className="flex flex-col gap-4 px-5 py-4">
          <SideBySide result={result} job={job} />
          <p className="text-[12.5px] leading-relaxed text-ink-2">{result.note}</p>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setResult(null)}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              {COPY.addLink.useDifferent}
            </button>
            <button
              type="button"
              data-testid="urlless-attach-anyway"
              onClick={() => onAttached(COPY.addLink.attached)}
              className="rounded-md border border-border-2 bg-surface-3 px-3 py-1.5 text-[12.5px] font-medium text-ink hover:bg-surface-2"
            >
              {COPY.addLink.attachAnyway}
            </button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title={TITLE[variant]} onClose={onClose} width={520}>
      <div className="flex flex-col gap-3 px-5 py-4">
        <div className="text-[12.5px] font-medium text-ink">
          {job.title} · {job.company}
        </div>
        {variant === "apply" ? (
          <p className="text-[12.5px] leading-relaxed text-ink-2">{COPY.addLink.applyBody}</p>
        ) : null}
        <input
          type="url"
          autoFocus
          data-testid="urlless-attach-input"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setResult(null);
          }}
          className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none"
        />
        {result?.kind === "unreadable" ? (
          <p
            className="rounded-md border border-warn/30 bg-warn/5 px-2.5 py-1.5 text-[11.5px] leading-snug text-warn"
            data-testid="urlless-attach-unreadable"
          >
            ⚠ {COPY.addLink.unreadable}
          </p>
        ) : checking ? (
          <p className="flex items-center gap-1.5 text-[11.5px] text-ink-3">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
            {COPY.addLink.checking}
          </p>
        ) : (
          <p className="text-[11.5px] leading-snug text-ink-4">↳ {HINT[variant]}</p>
        )}
        <div className="mt-1 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
          >
            {COPY.addJob.cancel}
          </button>
          {result?.kind === "unreadable" ? (
            <button
              type="button"
              data-testid="urlless-attach-anyway"
              onClick={() => onAttached(COPY.addLink.attached)}
              className="rounded-md border border-border-2 bg-surface-3 px-3 py-1.5 text-[12.5px] font-medium text-ink hover:bg-surface-2"
            >
              {COPY.addLink.attachAnyway}
            </button>
          ) : (
            <button
              type="button"
              disabled={checking || url.trim().length < 12}
              data-testid="urlless-attach-btn"
              onClick={attach}
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
            >
              {PRIMARY[variant]}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
