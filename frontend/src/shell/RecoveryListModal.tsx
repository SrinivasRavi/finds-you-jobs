// The one recovery-list modal (D-F2) for Trash, deleted Applications and deleted
// Contacts. Contacts pass no `deleteForever`: there is no permanent-delete
// endpoint for them.

import { useState, type ReactNode } from "react";

import { Avatar } from "./Avatar";
import { Modal, type ModalSize } from "./Modal";

export interface RecoveryRow {
  id: string;
  /** Name the avatar's initials come from (the company, or the contact). */
  avatarName: string;
  title: string;
  subtitle: ReactNode;
}

export interface RecoveryRestoreAction {
  label: string;
  testid?: string;
  onRun: (id: string) => void;
}

export interface RecoveryDeleteAction {
  label: string;
  /** Copy for the "no, back out" button of the two-step confirm. */
  cancelLabel: string;
  testid?: string;
  confirmTestid?: string;
  /** Native tooltip on the arming button (Job Trash carries one). */
  title?: string;
  onRun: (id: string) => void;
}

export function RecoveryListModal({
  title,
  onClose,
  size = "lg",
  bodyTestid,
  rowTestid,
  blurb,
  empty,
  rows,
  restore,
  deleteForever,
  footer,
}: {
  title: string;
  onClose: () => void;
  size?: ModalSize;
  bodyTestid: string;
  rowTestid?: string;
  /** Optional lead-in paragraph above the list. */
  blurb?: ReactNode;
  /** Shown instead of the list when nothing is recoverable. */
  empty: ReactNode;
  rows: RecoveryRow[];
  restore: RecoveryRestoreAction;
  deleteForever?: RecoveryDeleteAction;
  footer?: ReactNode;
}) {
  // Two-step confirm before anything irreversible (US-JB-11 ethos: the user
  // signs off on every irreversible action).
  const [confirmId, setConfirmId] = useState<string | null>(null);
  return (
    <Modal title={title} onClose={onClose} size={size} footer={footer}>
      <div data-testid={bodyTestid} className="px-5 py-4">
        {blurb ? <p className="mb-3 text-[11.5px] text-ink-3">{blurb}</p> : null}
        {rows.length === 0 ? (
          <p className="text-[13px] text-ink-3">{empty}</p>
        ) : (
          <ul className="space-y-2">
            {rows.map((row) => (
              <li
                key={row.id}
                data-testid={rowTestid}
                className="rounded-md border border-border px-4 py-3"
              >
                <div className="flex items-center gap-3">
                  <Avatar name={row.avatarName} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12.5px] font-medium text-ink">{row.title}</div>
                    <div className="truncate text-[11px] text-ink-3">{row.subtitle}</div>
                  </div>
                  <button
                    data-testid={restore.testid}
                    onClick={() => restore.onRun(row.id)}
                    className="shrink-0 rounded-md border border-border-2 px-2.5 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                  >
                    {restore.label}
                  </button>
                  {deleteForever ? (
                    <button
                      data-testid={deleteForever.testid}
                      title={deleteForever.title}
                      disabled={confirmId === row.id}
                      onClick={() => setConfirmId(row.id)}
                      className="shrink-0 rounded-md border border-bad/40 px-2.5 py-1 text-[11.5px] text-bad hover:bg-bad-wash disabled:opacity-40"
                    >
                      {deleteForever.label}
                    </button>
                  ) : null}
                </div>
                {/* The confirm gets its own line under the row. Squeezed in
                    beside the title it capped the warning at 260px and read as
                    cramped (maintainer, 2026-10-08). */}
                {deleteForever && confirmId === row.id ? (
                  <div className="mt-3 space-y-2 border-t border-border pt-3">
                    <div className="flex items-center gap-2">
                      <button
                        data-testid={deleteForever.confirmTestid}
                        onClick={() => {
                          deleteForever.onRun(row.id);
                          setConfirmId(null);
                        }}
                        className="rounded-md border border-bad/40 bg-bad px-2.5 py-1 text-[11.5px] font-medium text-white hover:opacity-90"
                      >
                        {deleteForever.label}
                      </button>
                      <button
                        onClick={() => setConfirmId(null)}
                        className="rounded-md border border-border-2 px-2.5 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                      >
                        {deleteForever.cancelLabel}
                      </button>
                    </div>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
