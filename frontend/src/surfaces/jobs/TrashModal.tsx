import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type Job } from "../../api/types";
import { RecoveryListModal } from "../../shell/RecoveryListModal";

// ─── Trash (US-JB-11) ─────────────────────────────────────────────────────────

export function TrashModal({
  trashed,
  onClose,
  onUndo,
  onDeleteForever,
  onEmpty,
}: {
  trashed: Job[];
  onClose: () => void;
  onUndo: (id: string) => void;
  onDeleteForever: (id: string) => void;
  onEmpty: () => void;
}) {
  // Two-step confirms before anything irreversible (US-JB-11 ethos: the user
  // signs off on every irreversible action). The per-row Delete-forever confirm
  // lives in the shared roster (duplication audit D-F2); `confirmEmpty` is this
  // modal's own — Empty Trash has no counterpart on the other two rosters.
  const { t } = useTranslation();
  const [confirmEmpty, setConfirmEmpty] = useState(false);

  return (
    <RecoveryListModal
      title={t("jobBoard.trashModal.title")}
      onClose={onClose}
      bodyTestid="trash-modal"
      empty={t("jobBoard.trashModal.empty")}
      rows={trashed.map((j) => ({
        id: j.id,
        avatarName: j.company,
        title: j.title,
        subtitle: t("jobBoard.trashModal.removedRecently", { company: j.company }),
      }))}
      restore={{
        label: t("jobBoard.trashModal.undo"),
        testid: "trash-undo-btn",
        onRun: onUndo,
      }}
      deleteForever={{
        label: t("jobBoard.trashModal.deleteForever"),
        cancelLabel: t("jobBoard.trashModal.cancel"),
        testid: "trash-delete-forever-btn",
        confirmTestid: "trash-delete-forever-confirm-btn",
        title: t("jobBoard.trashModal.deleteForever"),
        onRun: onDeleteForever,
      }}
      footer={
        /* "Delete all forever" gets its own line. Wedged next to the retention
           sentence it was a 2-word button in a 3-column squeeze, and the whole
           dialog read as cramped (maintainer, 2026-10-08). */
        <div className="space-y-2.5">
          <p className="text-[11.5px] leading-relaxed text-ink-3">
            {t("jobBoard.trashModal.retention")}
          </p>
          {confirmEmpty ? (
            <div className="space-y-2">
              <p className="text-[12px] text-warn">
                {t("jobBoard.trashModal.emptyConfirm", { count: trashed.length })}
              </p>
              <div className="flex items-center gap-2">
                <button
                  data-testid="trash-empty-confirm-btn"
                  onClick={() => {
                    onEmpty();
                    setConfirmEmpty(false);
                  }}
                  className="rounded-md border border-bad/40 bg-bad px-3 py-1.5 text-[12px] font-medium text-white hover:opacity-90"
                >
                  {t("jobBoard.trashModal.confirm")}
                </button>
                <button
                  onClick={() => setConfirmEmpty(false)}
                  className="rounded-md border border-border px-3 py-1.5 text-[12px] text-ink-2 hover:bg-surface-3"
                >
                  {t("jobBoard.trashModal.cancel")}
                </button>
              </div>
            </div>
          ) : (
            <button
              data-testid="trash-empty-btn"
              disabled={trashed.length === 0}
              onClick={() => setConfirmEmpty(true)}
              className="w-full rounded-md border border-bad/40 px-3 py-2 text-[12px] font-medium text-bad hover:bg-bad-wash disabled:opacity-40 disabled:hover:bg-transparent"
            >
              {t("jobBoard.trashModal.emptyTrash")}
            </button>
          )}
        </div>
      }
    />
  );
}
