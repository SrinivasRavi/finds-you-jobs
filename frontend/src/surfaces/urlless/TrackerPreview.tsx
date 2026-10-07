// Drawings 11 and 12 (sections 9.7 and 9.8): the tracker card for a job with no
// link, its detail Overview tab where "Open job posting" becomes "+ Add link",
// and the Apply slot that asks for the URL instead of launching a browser at a
// content hash. The prompt belongs before the `ApplyRun` row is written, so the
// Applier panel never shows a run that had no chance of working.

import { useState } from "react";

import { Avatar } from "../../shell/Avatar";
import { Icon } from "../../shell/icons";
import { Markdown } from "../../shell/Markdown";
import { Modal } from "../../shell/Modal";
import { sourceClasses } from "../jobFormat";
import { AddLinkDialog } from "./AddLinkDialog";
import { COPY, PASTED_JOB } from "./mock";

const SLOT = "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium";
const SLOT_IDLE = SLOT + " border-border-2 bg-surface text-ink-3";
const SLOT_DONE = SLOT + " border-good bg-good-wash text-good";

function Chips() {
  return (
    <>
      <span
        className={
          "inline-flex h-[16px] items-center rounded-full border px-1.5 text-[9.5px] " +
          sourceClasses(PASTED_JOB.sourceAdapter)
        }
      >
        {PASTED_JOB.sourceAdapter}
      </span>
      <span className="inline-flex h-[16px] items-center rounded-full border border-warn/40 bg-warn/10 px-1.5 text-[9.5px] text-warn">
        unverified
      </span>
    </>
  );
}

function DetailModal({ onClose, onAddLink }: { onClose: () => void; onAddLink: () => void }) {
  const tabs = ["Overview", "Notes", "Scoring", "Activity"];
  return (
    <Modal title={`${PASTED_JOB.title} · ${PASTED_JOB.company}`} onClose={onClose} width={640}>
      <div className="flex items-center gap-1 border-b border-border px-5">
        {tabs.map((tab, i) => (
          <span
            key={tab}
            className={
              "border-b-2 px-3 py-2 text-[12.5px] " +
              (i === 0 ? "border-accent font-medium text-ink" : "border-transparent text-ink-3")
            }
          >
            {tab}
          </span>
        ))}
      </div>
      <div className="space-y-4 px-5 py-4" data-testid="urlless-tracker-detail">
        <div className="flex items-start gap-3">
          <Avatar name={PASTED_JOB.company} size={10} shape="md" tone="raised" />
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-semibold leading-snug text-ink">{PASTED_JOB.title}</div>
            <div className="truncate text-[12px] text-ink-3">
              {[PASTED_JOB.company, PASTED_JOB.location, PASTED_JOB.workStyle].join(" · ")}
            </div>
          </div>
          <span className="inline-grid h-10 w-10 shrink-0 place-items-center rounded-full border border-border-2 text-[13px] font-semibold text-ink-3">
            {PASTED_JOB.score}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-[12px]">
          <button
            type="button"
            data-testid="urlless-tracker-add-link"
            onClick={onAddLink}
            className="inline-flex items-center gap-1 font-medium text-accent hover:underline"
          >
            <Icon name="plus" size={12} strokeWidth={2} />
            {COPY.detail.addLink}
          </button>
          <span className="text-ink-3">{PASTED_JOB.salary}</span>
          <Chips />
        </div>
        <details className="rounded-md border border-border bg-surface-2">
          <summary className="cursor-pointer px-3 py-2 text-[12px] font-medium text-ink-2">
            Job description
          </summary>
          <div className="border-t border-border px-3 py-2 text-[12.5px] leading-relaxed text-ink-2">
            <Markdown md={PASTED_JOB.description} />
          </div>
        </details>
        <div className="flex items-center gap-3">
          <span className="text-[12px] text-ink-3">Priority</span>
          <span className="rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink">
            P1
          </span>
          <span className="ml-auto text-[11px] text-ink-4">#A4F2C1</span>
          <span className="text-[12px] text-ink-3">Stage · Applied</span>
        </div>
        <div className="flex gap-2">
          <button className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3">
            View resume
          </button>
          <button className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3">
            Generate cover letter
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function TrackerPreview({ onToast }: { onToast: (note: string) => void }) {
  const [detail, setDetail] = useState(false);
  const [dialog, setDialog] = useState<"promote" | "apply" | null>(null);
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-surface-2 p-6">
      <div className="w-[260px] rounded-xl border border-border bg-surface p-3">
        <div className="mb-2 text-[12px] font-semibold text-ink-3">Applied</div>
        <div
          onClick={() => setDetail(true)}
          data-testid="urlless-tracker-card"
          className="cursor-pointer rounded-lg border border-border bg-surface p-3 shadow-sm hover:border-border-2"
        >
          <div className="flex items-start gap-2">
            <Avatar name={PASTED_JOB.company} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12.5px] font-semibold text-ink">{PASTED_JOB.title}</div>
              <div className="truncate text-[11px] text-ink-3">{PASTED_JOB.company}</div>
            </div>
            <Icon name="moreV" size={16} strokeWidth={2} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-semibold text-ink-3">{PASTED_JOB.score}</span>
            <Chips />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1" onClick={(e) => e.stopPropagation()}>
            <span className={SLOT_DONE}>
              <Icon name="check" size={10} strokeWidth={3} />
              Resume
            </span>
            <span className={SLOT_IDLE}>Cover letter</span>
            <span className={SLOT_IDLE}>Referrals</span>
            <button
              type="button"
              data-testid="urlless-apply-slot"
              onClick={() => setDialog("apply")}
              className={SLOT_IDLE + " hover:bg-surface-3"}
            >
              Apply
            </button>
          </div>
          <div className="mt-2 text-[10px] text-ink-4">4d in column · touched 10/05</div>
        </div>
      </div>
      <p className="mt-3 max-w-[420px] text-[11.5px] leading-snug text-ink-4">
        Click the card for the Overview tab (drawing 11). Click Apply for the prompt that lands
        before the run row is written (drawing 12).
      </p>

      {detail ? (
        <DetailModal
          onClose={() => setDetail(false)}
          onAddLink={() => {
            setDetail(false);
            setDialog("promote");
          }}
        />
      ) : null}
      {dialog ? (
        <AddLinkDialog
          job={PASTED_JOB}
          variant={dialog}
          onClose={() => setDialog(null)}
          onAttached={(note) => {
            setDialog(null);
            onToast(note);
          }}
        />
      ) : null}
    </div>
  );
}
