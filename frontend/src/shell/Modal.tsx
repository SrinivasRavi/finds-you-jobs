// Shared modal shell — ports the prototype dialog pattern (backdrop +
// centered card, close on backdrop/escape). Used by Add-by-URL, the resume
// trio, cover letter, guidance, and the application detail modal.

import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Icon } from "./icons";

export function Modal({
  title,
  onClose,
  children,
  width = 520,
  footer,
  headerExtra,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  width?: number;
  footer?: ReactNode;
  /** Rendered in the title bar next to the × close button (e.g. Share). */
  headerExtra?: ReactNode;
}) {
  const { t } = useTranslation();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-[rgba(24,24,27,0.42)] backdrop-blur-[1.5px]"
        onClick={onClose}
        data-testid="modal-backdrop"
      />
      <div
        className="fixed left-1/2 top-1/2 z-50 flex max-h-[94vh] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-[14px] border border-border bg-surface shadow-[0_50px_100px_rgba(0,0,0,0.4)]"
        style={{ width, maxWidth: "96vw" }}
        role="dialog"
        aria-label={title}
      >
        <div className="flex items-center gap-3 border-b border-border px-5 py-4">
          <h2 className="m-0 text-[16px] font-semibold text-ink">{title}</h2>
          <div className="ml-auto flex items-center gap-2">
            {headerExtra}
            <button className="text-ink-3 hover:text-ink" aria-label={t("shell.close")} onClick={onClose}>
              <Icon name="x" size={18} strokeWidth={2} />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
        {footer ? <div className="border-t border-border px-5 py-3">{footer}</div> : null}
      </div>
    </>
  );
}
