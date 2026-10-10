// Shared modal shell — ports the prototype dialog pattern (backdrop +
// centered card, close on backdrop/escape). Used by Add-a-job-posting, the
// resume trio, cover letter, guidance, and the application detail modal.
//
// 5 sizes, named, and nothing picks its own number (maintainer, 2026-10-08:
// "use few standard popup modal sizes, not random ones all the time"). The
// tree had 11 distinct widths between 440 and 1840 chosen per caller, so 2
// dialogs doing the same job came out different sizes.

import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Icon } from "./icons";

/** The size scale. `sm` confirms, `md` forms, `lg` detail panes and rosters,
 *  `xl` editors and workbenches, `full` the 2-column and browser surfaces. */
export const MODAL_WIDTHS = { sm: 440, md: 560, lg: 780, xl: 1200, full: 1840 } as const;

export type ModalSize = keyof typeof MODAL_WIDTHS;

export function Modal({
  title,
  onClose,
  children,
  size = "md",
  footer,
  headerExtra,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  size?: ModalSize;
  footer?: ReactNode;
  /** Rendered in the title bar next to the × close button (e.g. Share). */
  headerExtra?: ReactNode;
}) {
  const width = MODAL_WIDTHS[size];
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
