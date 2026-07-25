// Deleted-applications modal with per-row restore / two-step Delete forever.
// (Extracted from Tracker.tsx 2026-07-25, F-M6 monolith split — pure move,
// zero behavior change.) Not memoized: it mounts only while open.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useDeleteApplicationForever, useUnarchiveApplication } from "../../api/queries";
import type { Application } from "../../api/types";
import { Modal } from "../../shell/Modal";
import { initials } from "../jobFormat";

export function ArchiveModal({ archived, onClose }: { archived: Application[]; onClose: () => void }) {
  const { t } = useTranslation();
  const unarchive = useUnarchiveApplication();
  const deleteForever = useDeleteApplicationForever();
  // Two-step per-row confirm before the irreversible delete — same pattern as
  // the Job Board's Trash modal (US-JB-11 ethos: the user signs off on every
  // irreversible action).
  const [confirmId, setConfirmId] = useState<string | null>(null);
  return (
    <Modal title={t("tracker.deletedApplications")} onClose={onClose} width={520}>
      <div data-testid="deleted-applications-modal" className="px-5 py-4">
        {archived.length === 0 ? (
          <p className="text-[13px] text-ink-3">{t("tracker.archiveModal.empty")}</p>
        ) : (
          <ul className="space-y-2">
            {archived.map((a) => (
              <li key={a.id} className="flex items-center gap-3 rounded-md border border-border px-3 py-2">
                <div className="grid h-8 w-8 place-items-center rounded bg-surface-2 text-[11px] font-semibold text-ink-2">
                  {initials(a.job.company)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium text-ink">{a.job.title}</div>
                  <div className="text-[11px] text-ink-3">{a.job.company} · {t("tracker.archiveModal.deletedRecently")}</div>
                </div>
                {confirmId === a.id ? (
                  <>
                    <button
                      data-testid="deleted-app-delete-forever-confirm-btn"
                      onClick={() => {
                        deleteForever.mutate(a.id);
                        setConfirmId(null);
                      }}
                      className="rounded-md border border-bad/40 bg-bad px-2 py-1 text-[11.5px] font-medium text-white hover:opacity-90"
                    >
                      {t("tracker.archiveModal.deleteForever")}
                    </button>
                    <button
                      onClick={() => setConfirmId(null)}
                      className="rounded-md border border-border px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                    >
                      {t("tracker.cancel")}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      data-testid="deleted-app-restore-btn"
                      onClick={() => unarchive.mutate(a.id)}
                      className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                    >
                      {t("tracker.archiveModal.restore")}
                    </button>
                    <button
                      data-testid="deleted-app-delete-forever-btn"
                      onClick={() => setConfirmId(a.id)}
                      className="rounded-md border border-bad/40 px-2 py-1 text-[11.5px] text-bad hover:bg-bad-wash"
                    >
                      {t("tracker.archiveModal.deleteForever")}
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
