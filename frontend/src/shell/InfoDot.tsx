// A tiny "ⓘ" info affordance: plain-English copy stays in the main text; the
// precise technical detail lives one click away here. Keeps UI approachable for
// non-technical users without hiding the exact terms from technical ones.

import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

export function InfoDot({ children, label }: { children: ReactNode; label?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    // Shows on HOVER (and keyboard focus), hides when the pointer leaves — the
    // tooltip is a descendant, so moving onto it keeps it open. Nudged up ~2px so
    // the "i" sits with the text, not below it.
    <span
      className="relative -top-[2px] ml-1 inline-block align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={label ?? t("shell.moreDetail")}
        aria-expanded={open}
        data-testid="info-dot"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="inline-grid h-[15px] w-[15px] place-items-center rounded-full border border-border-2 text-[9.5px] font-semibold leading-none text-ink-3 hover:bg-surface-3 hover:text-ink-2"
      >
        i
      </button>
      {open ? (
        <span
          role="tooltip"
          data-testid="info-dot-tip"
          className="absolute left-1/2 top-[16px] z-30 w-64 -translate-x-1/2 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-left text-[11px] font-normal normal-case leading-snug text-ink-2 shadow-lg"
        >
          {children}
        </span>
      ) : null}
    </span>
  );
}
