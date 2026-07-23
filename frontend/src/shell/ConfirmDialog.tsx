// A small yes/no confirmation modal (maintainer 2026-07-23) — used for the
// "Re-score all N jobs with AI?" prompt and any other reversible confirm.
import { useTranslation } from "react-i18next";

import { Modal } from "./Modal";

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal title={title} onClose={onCancel} width={440}>
      <div className="flex flex-col gap-4 px-5 py-4" data-testid="confirm-dialog">
        <p className="text-[13px] leading-relaxed text-ink-2">{body}</p>
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            data-testid="confirm-cancel"
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2 hover:text-ink disabled:opacity-50"
          >
            {cancelLabel ?? t("shell.cancel")}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            data-testid="confirm-ok"
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-60"
          >
            {confirmLabel ?? t("shell.confirm")}
          </button>
        </div>
      </div>
    </Modal>
  );
}
