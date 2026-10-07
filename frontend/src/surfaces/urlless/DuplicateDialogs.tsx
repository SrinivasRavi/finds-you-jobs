// Drawings 9 and 10 (section 9.6): the duplicate prompt and the merge dialog.
// The prompt rides on the scanned card, because that's the one that appeared
// without the user asking. The merge is never automatic, and when both rows
// carry an application it refuses and asks, because `uq_applications_job`
// permits 1 application per job and guessing with a pipeline is the worst
// available option (section 5.5).

import { Modal } from "../../shell/Modal";
import { COPY } from "./mock";

export function DuplicatePrompt({
  onCompare,
  onDismiss,
}: {
  onCompare: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      className="mt-2 rounded-md border border-warn/30 bg-warn/5 px-2.5 py-2"
      data-testid="urlless-duplicate-prompt"
    >
      <p className="text-[11.5px] leading-snug text-warn">{COPY.duplicate.prompt}</p>
      <div className="mt-1.5 flex gap-2">
        <button
          type="button"
          onClick={onCompare}
          className="rounded-md border border-warn/40 bg-surface px-2 py-1 text-[11px] font-medium text-warn hover:bg-warn/10"
        >
          {COPY.duplicate.compare}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-md border border-border-2 bg-surface px-2 py-1 text-[11px] text-ink-2 hover:bg-surface-3"
        >
          {COPY.duplicate.different}
        </button>
      </div>
    </div>
  );
}

function Ledger({ heading, from, rows }: { heading: string; from: string; rows: [string, string][] }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-x-4 gap-y-1 text-[12.5px]">
      <div className="font-medium text-ink">{heading}</div>
      <div className="text-ink-2">{from}</div>
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <div className="pl-3 text-ink-3">{label}</div>
          <div className="text-ink-2">{value}</div>
        </div>
      ))}
    </div>
  );
}

export function MergeDialog({
  refuse = false,
  onClose,
  onMerge,
  onKeepBoth,
}: {
  refuse?: boolean;
  onClose: () => void;
  onMerge: () => void;
  onKeepBoth: () => void;
}) {
  if (refuse) {
    return (
      <Modal title={COPY.duplicate.mergeTitle} onClose={onClose} width={520}>
        <div className="flex flex-col gap-4 px-5 py-4" data-testid="urlless-merge-refusal">
          <p className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-[12.5px] leading-relaxed text-warn">
            ⚠ {COPY.duplicate.refusal}
          </p>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              {COPY.duplicate.openMonday}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              {COPY.duplicate.openOther}
            </button>
          </div>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title={COPY.duplicate.mergeTitle} onClose={onClose} width={520}>
      <div className="flex flex-col gap-4 px-5 py-4" data-testid="urlless-merge-dialog">
        <Ledger
          heading={COPY.duplicate.keeping}
          from={COPY.duplicate.keepingFrom}
          rows={[
            [COPY.duplicate.stage, COPY.duplicate.stageValue],
            [COPY.duplicate.documents, COPY.duplicate.documentsValue],
            [COPY.duplicate.notes, COPY.duplicate.notesValue],
          ]}
        />
        <Ledger
          heading={COPY.duplicate.taking}
          from={COPY.duplicate.takingFrom}
          rows={[
            [COPY.duplicate.description, COPY.duplicate.descriptionValue],
            [COPY.duplicate.link, COPY.duplicate.linkValue],
            [COPY.duplicate.source, COPY.duplicate.sourceValue],
          ]}
        />
        <p className="text-[12px] text-ink-3">{COPY.duplicate.footnote}</p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onKeepBoth}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
          >
            {COPY.duplicate.keepBoth}
          </button>
          <button
            type="button"
            data-testid="urlless-merge-btn"
            onClick={onMerge}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
          >
            {COPY.duplicate.merge}
          </button>
        </div>
      </div>
    </Modal>
  );
}
