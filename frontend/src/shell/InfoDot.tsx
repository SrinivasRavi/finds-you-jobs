// A tiny "ⓘ" info affordance: plain-English copy stays in the main text; the
// precise technical detail lives one click away here. Keeps UI approachable for
// non-technical users without hiding the exact terms from technical ones.

import { type ReactNode, useState } from "react";

export function InfoDot({ children, label = "More detail" }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-block align-middle">
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        data-testid="info-dot"
        onClick={() => setOpen((v) => !v)}
        onBlur={() => setOpen(false)}
        className="ml-1 inline-grid h-[15px] w-[15px] place-items-center rounded-full border border-border-2 text-[9.5px] font-semibold leading-none text-ink-3 hover:bg-surface-3 hover:text-ink-2"
      >
        i
      </button>
      {open ? (
        <span
          role="tooltip"
          data-testid="info-dot-tip"
          className="absolute left-1/2 top-[20px] z-30 w-64 -translate-x-1/2 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-left text-[11px] font-normal leading-snug text-ink-2 shadow-lg"
        >
          {children}
        </span>
      ) : null}
    </span>
  );
}
