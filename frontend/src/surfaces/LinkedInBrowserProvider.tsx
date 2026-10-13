// The LinkedIn browser modal and its app-level opener: the watch-only surface
// beside the "LinkedIn actions" queue, in a maximum-size dialog. Mounted once in
// the Layout because the Tracker's popup and every send path open it too; hoisting
// `useBrowserOps` here keeps 1 SSE subscription for the session. Add-on side: it
// names the vendor (plugin-architecture.md section 12.2).

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useLinkedInSession } from "../api/queries";
import { Modal } from "../shell/Modal";
import { BrowserOpPlan, opBusy, useBrowserOps, type BrowserOps } from "./BrowserOpPlan";
import { BrowserSurface } from "./BrowserSurface";
import {
  HEADER_PILL_CLS,
  HEADER_PILL_LABEL,
  linkedInStatusPill,
} from "./linkedInStatus";

// The broker surface slug this modal attaches to. Owned by the add-on side —
// never spelled inside the shared BrowserSurface / screencast components.
export const LINKEDIN_SURFACE_SLUG = "linkedin";

// The surface's home origin. The env override exists for the e2e stack alone,
// so the suite can point the view at a LOCAL fixture origin and never touch
// linkedin.com; a normal build never sets it.
export const LINKEDIN_ORIGIN: string =
  (import.meta.env.VITE_LINKEDIN_ORIGIN as string | undefined) ||
  "https://www.linkedin.com/";

interface LinkedInBrowserValue {
  /** Open the LinkedIn browser modal over the current surface. A caller that closed
   *  its own modal to get here (the referrals popup) passes `onBack`, which puts a
   *  Back control in the header; direct callers get none. */
  open: (opts?: { onBack?: () => void }) => void;
  close: () => void;
  isOpen: boolean;
  /** The session-long op feed — the pill's busy state + the queue panel. */
  ops: BrowserOps;
}

const LinkedInBrowserContext = createContext<LinkedInBrowserValue | null>(null);

export function useLinkedInBrowser(): LinkedInBrowserValue {
  const ctx = useContext(LinkedInBrowserContext);
  if (ctx === null) {
    throw new Error("useLinkedInBrowser requires a <LinkedInBrowserProvider>");
  }
  return ctx;
}

export function LinkedInBrowserProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  // Held in a ref-like state slot rather than passed through the modal's props
  // from the caller, because the caller has unmounted by the time it matters.
  const [onBack, setOnBack] = useState<(() => void) | null>(null);
  const ops = useBrowserOps();
  const value = useMemo<LinkedInBrowserValue>(
    () => ({
      open: (opts) => {
        setOnBack(() => opts?.onBack ?? null);
        setIsOpen(true);
      },
      close: () => {
        setOnBack(null);
        setIsOpen(false);
      },
      isOpen,
      ops,
    }),
    [isOpen, ops],
  );
  return (
    <LinkedInBrowserContext.Provider value={value}>
      {children}
      {/* Mounted only while open: the screencast socket attaches on mount and
          detaches cleanly on close; the surface, its Chrome, and any running
          op all outlive the dialog (broker detach is identity-checked). */}
      {isOpen && (
        <LinkedInBrowserModal
          ops={ops}
          onClose={() => {
            setOnBack(null);
            setIsOpen(false);
          }}
          onBack={
            onBack
              ? () => {
                  setIsOpen(false);
                  setOnBack(null);
                  onBack();
                }
              : undefined
          }
        />
      )}
    </LinkedInBrowserContext.Provider>
  );
}

function LinkedInBrowserModal({
  ops,
  onClose,
  onBack,
}: {
  ops: BrowserOps;
  onClose: () => void;
  onBack?: () => void;
}) {
  const { t } = useTranslation();
  const session = useLinkedInSession();
  const enabled = Boolean(session.data?.enabled);
  const pill = session.data ? linkedInStatusPill(session.data.status) : null;

  return (
    <Modal
      title={t("networking.linkedinModal.title")}
      onClose={onClose}
      size="full"
      headerExtra={
        <>
          {onBack ? (
            <button
              type="button"
              data-testid="linkedin-modal-back"
              className="inline-flex h-[22px] items-center gap-1 rounded-full border border-border px-2 text-[11.5px] text-ink-2 hover:bg-surface-3"
              onClick={onBack}
            >
              {t("networking.linkedinModal.back")}
            </button>
          ) : null}
          {pill ? (
          <span
            data-testid="linkedin-modal-status"
            className={`inline-flex h-[22px] items-center gap-[5px] whitespace-nowrap rounded-full border px-2 text-[11.5px] font-medium ${HEADER_PILL_CLS[pill.tone]}`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {t(HEADER_PILL_LABEL[pill.state])}
            </span>
          ) : null}
        </>
      }
    >
      {enabled ? (
        <div className="flex h-[82vh] flex-col" data-testid="linkedin-view">
          <div className="flex min-h-0 flex-1">
            <div className="flex min-w-0 flex-1 flex-col">
              <BrowserSurface
                surface={LINKEDIN_SURFACE_SLUG}
                origin={LINKEDIN_ORIGIN}
                autoHome={!opBusy(ops.current)}
              />
            </div>
            <BrowserOpPlan ops={ops} />
          </div>
        </div>
      ) : (
        // Entry points hide while Referral Outreach is off, but the toggle can
        // flip with the dialog open — the honest one-liner, never a dead surface.
        <div className="flex h-[82vh] items-center justify-center px-8 text-center">
          <p className="text-[13px] text-ink-3" data-testid="linkedin-view-disabled">
            {t("networking.linkedinView.disabled")}
          </p>
        </div>
      )}
    </Modal>
  );
}
