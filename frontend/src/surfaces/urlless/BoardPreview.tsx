// Drawings 4, 5, 6, 9 and 16 (sections 9.3, 9.4, 9.6, 9.13): the board list
// with the `no link` chip and the Link filter group, the trust chip that is the
// first reader of the stored `trust_flags`, the detail action row where
// "Open posting" becomes "Add link", the duplicate prompt on the scanned card,
// and the empty board's 1 new button.

import { useState } from "react";

import { Avatar } from "../../shell/Avatar";
import { Chip } from "../../shell/FilterRow";
import { Icon } from "../../shell/icons";
import { Markdown } from "../../shell/Markdown";
import { sourceClasses } from "../jobFormat";
import { AddJobModal } from "./AddJobModal";
import { AddLinkDialog, type LinkDialogVariant } from "./AddLinkDialog";
import { DuplicatePrompt, MergeDialog } from "./DuplicateDialogs";
import { BOARD_ROWS, COPY, SCANNED_JOB, type PreviewJob } from "./mock";

type LinkFilter = "ALL" | "HAS" | "NONE";

function TrustChip() {
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        data-testid="urlless-trust-chip"
        aria-label={COPY.card.trustLabel}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="inline-grid h-[16px] w-[16px] place-items-center rounded-full border border-warn/40 bg-warn/10 text-[9.5px] font-semibold text-warn"
      >
        ?
      </button>
      {open ? (
        <span
          role="tooltip"
          data-testid="urlless-trust-tip"
          className="absolute left-1/2 top-[18px] z-30 w-64 -translate-x-1/2 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-left text-[11px] font-normal leading-snug text-ink-2 shadow-lg"
        >
          <span className="block font-semibold text-ink">{COPY.card.trustLabel}</span>
          {COPY.card.trustTip}
        </span>
      ) : null}
    </span>
  );
}

function Row({
  job,
  selected,
  onSelect,
  duplicate,
  onCompare,
  onDismissDuplicate,
}: {
  job: PreviewJob;
  selected: boolean;
  onSelect: () => void;
  duplicate: boolean;
  onCompare: () => void;
  onDismissDuplicate: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      data-testid="urlless-board-row"
      className={
        "cursor-pointer border-b border-border px-4 py-3 " +
        (selected ? "bg-accent-wash/40" : "hover:bg-surface-2")
      }
    >
      <div className="flex items-start gap-3">
        <Avatar name={job.company} size={8} shape="md" tone="brand" decorative />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold text-ink">{job.title}</div>
          <div className="truncate text-[11.5px] text-ink-2">
            {[job.company, job.location, job.workStyle].filter(Boolean).join(" · ")}
          </div>
          <div className="text-[11.5px] text-ink-3">
            {[job.salary, job.postedAgo, job.applicants ? `${job.applicants} applicants` : ""]
              .filter(Boolean)
              .join(" · ")}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {job.tags.map((tag) => (
              <span
                key={tag}
                className="inline-flex h-[16px] items-center rounded-full bg-surface-3 px-1.5 text-[9.5px] text-ink-2"
              >
                {tag}
              </span>
            ))}
            <span
              className={
                "inline-flex h-[16px] items-center rounded-full border px-1.5 text-[9.5px] " +
                sourceClasses(job.sourceAdapter)
              }
            >
              {job.sourceAdapter}
            </span>
            {job.link === null ? (
              <>
                <span
                  data-testid="urlless-no-link-chip"
                  className="inline-flex h-[16px] items-center rounded-full border border-border-2 bg-surface-2 px-1.5 text-[9.5px] text-ink-3"
                >
                  {COPY.card.noLink}
                </span>
                <TrustChip />
              </>
            ) : null}
          </div>
          {duplicate ? (
            <DuplicatePrompt onCompare={onCompare} onDismiss={onDismissDuplicate} />
          ) : null}
        </div>
        <span
          className={
            "shrink-0 text-[15px] font-semibold " +
            (job.scorer === "keyword" ? "text-ink-3" : "text-accent")
          }
        >
          {job.score}
        </span>
      </div>
    </div>
  );
}

function ActionRow({ job, onAddLink, onWatch }: { job: PreviewJob; onAddLink: () => void; onWatch: () => void }) {
  const pill =
    "inline-flex h-[30px] items-center justify-center gap-1.5 rounded-7 border px-3 text-[12px] font-medium";
  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-border bg-surface px-6 py-3">
      <button className={pill + " border-accent bg-accent text-white hover:bg-accent-ink"}>
        <Icon name="bookmark" size={14} strokeWidth={2} />
        Save
      </button>
      <button className={pill + " border-border-2 bg-surface text-ink-2 hover:bg-surface-3"}>
        <Icon name="trash" size={14} strokeWidth={2} />
        Remove
      </button>
      <div className="flex-1" />
      {job.link === null ? (
        <button
          type="button"
          data-testid="urlless-watch-company"
          onClick={onWatch}
          className={pill + " border-border-2 bg-surface text-ink-3 hover:bg-surface-3"}
        >
          {COPY.detail.watchCompany}
        </button>
      ) : (
        <span className={pill + " border-border-2 bg-surface text-ink-3"}>
          {COPY.detail.showMore}
        </span>
      )}
      {job.link === null ? (
        <button
          type="button"
          data-testid="urlless-add-link"
          onClick={onAddLink}
          className="inline-flex h-[30px] items-center gap-1 rounded-7 px-3 text-[12px] font-medium text-accent hover:bg-accent-wash"
        >
          <Icon name="plus" size={13} strokeWidth={2} />
          {COPY.detail.addLink}
        </button>
      ) : (
        <a
          href={job.link}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-[30px] items-center gap-1 rounded-7 px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
        >
          {COPY.detail.openPosting}
        </a>
      )}
    </div>
  );
}

function EmptyBoard({ onAdd }: { onAdd: () => void }) {
  return (
    <div
      className="grid h-full place-items-center px-6 text-center text-[12.5px] text-ink-3"
      data-testid="urlless-empty-board"
    >
      <div className="flex flex-col items-center gap-1">
        <div className="font-medium text-ink-2">{COPY.empty.noMatches}</div>
        <div className="text-ink-3">{COPY.empty.widen}</div>
        <div className="text-ink-4">{COPY.empty.lastRefresh}</div>
        <button
          type="button"
          data-testid="urlless-empty-add"
          onClick={onAdd}
          className="mt-4 rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
        >
          {COPY.empty.addJob}
        </button>
        <div className="mt-1 text-ink-4">{COPY.empty.gotOne}</div>
      </div>
    </div>
  );
}

export function BoardPreview({
  empty = false,
  showDuplicate = false,
  onToast,
}: {
  empty?: boolean;
  showDuplicate?: boolean;
  onToast: (note: string) => void;
}) {
  const [linkFilter, setLinkFilter] = useState<LinkFilter>("ALL");
  const [selectedId, setSelectedId] = useState(BOARD_ROWS[0]!.id);
  const [dialog, setDialog] = useState<LinkDialogVariant | null>(null);
  const [merge, setMerge] = useState<"open" | "refused" | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  const rows = BOARD_ROWS.filter((j) =>
    linkFilter === "ALL" ? true : linkFilter === "HAS" ? j.link !== null : j.link === null,
  );
  const selected = BOARD_ROWS.find((j) => j.id === selectedId) ?? BOARD_ROWS[0]!;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-[45px] flex-wrap items-center justify-end gap-2 border-b border-border bg-surface px-5 py-2">
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] text-ink-4">Status</span>
          <Chip active onClick={() => undefined}>
            All
          </Chip>
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5" data-testid="urlless-link-filter">
          <span className="text-[11.5px] text-ink-4">{COPY.card.linkFilter}</span>
          {(
            [
              ["ALL", COPY.card.linkAll],
              ["HAS", COPY.card.linkHas],
              ["NONE", COPY.card.linkNone],
            ] as [LinkFilter, string][]
          ).map(([value, label]) => (
            <Chip key={value} active={linkFilter === value} onClick={() => setLinkFilter(value)}>
              {label}
            </Chip>
          ))}
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] text-ink-4">Sort</span>
          <Chip active onClick={() => undefined}>
            Match score
          </Chip>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex w-[38%] min-w-0 flex-col overflow-y-auto border-r border-border">
          {empty ? (
            <EmptyBoard onAdd={() => setAddOpen(true)} />
          ) : (
            rows.map((job) => (
              <Row
                key={job.id}
                job={job}
                selected={job.id === selected.id}
                onSelect={() => setSelectedId(job.id)}
                duplicate={showDuplicate && job.id === SCANNED_JOB.id && !dismissed}
                onCompare={() => setMerge("open")}
                onDismissDuplicate={() => {
                  setDismissed(true);
                  onToast(COPY.duplicate.dismissed);
                }}
              />
            ))
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
          {empty ? null : (
            <>
              <div className="flex items-start gap-4 px-6 pt-6">
                <Avatar name={selected.company} size={14} shape="md" tone="brand" decorative />
                <div className="min-w-0 flex-1">
                  <h2 className="truncate text-[17px] font-semibold leading-tight text-ink">
                    {selected.title}
                  </h2>
                  <div className="mt-1 text-[12.5px] text-ink-2">
                    {[selected.company, selected.location, selected.workStyle, selected.salary]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </div>
              </div>
              <ActionRow
                job={selected}
                onAddLink={() => setDialog("promote")}
                onWatch={() => setDialog("watch")}
              />
              <div className="grid grid-cols-2 gap-6 px-6 py-5">
                <Markdown md={selected.description} />
                <aside className="rounded-lg border border-border bg-surface-2 p-4">
                  <h3 className="mb-2 text-[12px] font-semibold text-ink-3">
                    {selected.scorer === "keyword" ? "Keywords scored" : "Match score"}
                  </h3>
                  <ul className="space-y-1.5 text-[12.5px] text-ink-2">
                    {selected.reasons.map((r) => (
                      <li key={r} className="flex gap-1.5">
                        <span className="mt-1 size-1 shrink-0 rounded-full bg-accent" aria-hidden="true" />
                        <span>{r}</span>
                      </li>
                    ))}
                  </ul>
                </aside>
              </div>
            </>
          )}
        </div>
      </div>

      {dialog ? (
        <AddLinkDialog
          job={selected}
          variant={dialog}
          onClose={() => setDialog(null)}
          onAttached={(note) => {
            setDialog(null);
            onToast(note);
          }}
        />
      ) : null}
      {merge ? (
        <MergeDialog
          refuse={merge === "refused"}
          onClose={() => setMerge(null)}
          onKeepBoth={() => {
            setMerge(null);
            setDismissed(true);
            onToast(COPY.duplicate.dismissed);
          }}
          onMerge={() => {
            setMerge(null);
            setDismissed(true);
            onToast(COPY.duplicate.merged);
          }}
        />
      ) : null}
      {addOpen ? (
        <AddJobModal
          onClose={() => setAddOpen(false)}
          onSaved={(draft) => {
            setAddOpen(false);
            onToast(`Added "${draft.title}" to the board.`);
          }}
        />
      ) : null}
      {showDuplicate ? (
        <div className="border-t border-border bg-surface px-5 py-2 text-[11.5px] text-ink-3">
          Both rows in the tracker?{" "}
          <button
            type="button"
            data-testid="urlless-merge-refuse-btn"
            onClick={() => setMerge("refused")}
            className="text-accent hover:underline"
          >
            Show the merge refusal (drawing 10)
          </button>
        </div>
      ) : null}
    </div>
  );
}
