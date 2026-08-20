// The LinkedIn browser modal + its app-level opener (maintainer, 2026-08-16;
// it was a left-rail destination before, and a tab inside Networking before
// that). The live watch-only surface on the left, the "LinkedIn actions"
// queue panel on the right, in a maximum-size dialog (the tailored-resume
// sizing: the shell clamps to 96vw/94vh, backdrop + × stay visible).
//
// Mounted ONCE in the Layout, above every routed surface, because the modal
// opens from more than Networking: the Tracker's referrals popup and any
// send path land here too. Hoisting `useBrowserOps` into the provider keeps
// ONE SSE subscription + ledger seed alive for the whole session, so the
// Networking pill's busy state and the queue panel share state and nothing
// resets when the modal closes.
//
// Add-on-side by design: this file names the vendor (slug, origin, labels);
// the shared BrowserSurface / screencast components it mounts stay
// vendor-agnostic (`plugin-architecture.md` section 12.2).

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useLinkedInSession } from "../api/queries";
import { Modal } from "../shell/Modal";
import { BrowserOpPlan, opBusy, useBrowserOps, type BrowserOps } from "./BrowserOpPlan";
import { BrowserSurface } from "./BrowserSurface";
import { type LinkedInPillTone, linkedInStatusPill } from "./linkedInStatus";

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
  /** Open the LinkedIn browser modal (in place, over the current surface). */
  open: () => void;
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
  const ops = useBrowserOps();
  const value = useMemo<LinkedInBrowserValue>(
    () => ({
      open: () => setIsOpen(true),
      close: () => setIsOpen(false),
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
      {isOpen && <LinkedInBrowserModal ops={ops} onClose={() => setIsOpen(false)} />}
    </LinkedInBrowserContext.Provider>
  );
}

// The modal's own status chip (D-F8: only status→semantics is shared via
// linkedInStatusPill; each surface keeps its own chrome and copy).
const HEADER_PILL_CLS: Record<LinkedInPillTone, string> = {
  good: "bg-good-wash border-good text-good",
  warn: "bg-warn-wash border-warn text-warn",
  bad: "bg-bad-wash border-bad text-bad",
};
const HEADER_PILL_LABEL: Record<string, string> = {
  connected: "networking.linkedinPill.connected",
  connecting: "networking.linkedinPill.connecting",
  backingOff: "networking.linkedinPill.backingOff",
  expired: "networking.linkedinPill.expired",
  disconnected: "networking.linkedinPill.connect",
};

function LinkedInBrowserModal({
  ops,
  onClose,
}: {
  ops: BrowserOps;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const session = useLinkedInSession();
  const enabled = Boolean(session.data?.enabled);
  const pill = session.data ? linkedInStatusPill(session.data.status) : null;

  return (
    <Modal
      title={t("networking.linkedinModal.title")}
      onClose={onClose}
      width={1840}
      headerExtra={
        pill ? (
          <span
            data-testid="linkedin-modal-status"
            className={`inline-flex h-[22px] items-center gap-[5px] whitespace-nowrap rounded-full border px-2 text-[11.5px] font-medium ${HEADER_PILL_CLS[pill.tone]}`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            {t(HEADER_PILL_LABEL[pill.state])}
          </span>
        ) : null
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
