// The accent "+ Add …" action that sits at the right end of the Job Board,
// Applications, and Networking headers. A FIXED width + left-aligned content
// keeps the "＋ Add a " prefix aligned pixel-for-pixel when you switch tabs:
// each is the rightmost child of an `ml-auto` group in a `px-5` header, so they
// share a right edge — a shared width gives them a shared left edge too. Shorter
// labels simply get extra padding on the right.

import { useTranslation } from "react-i18next";

import { Icon } from "./icons";

// Pinned to the longest label's natural width ("Add a contact by URL" ≈ 170px,
// measured 2026-07-23). The longest sits snug; only the shorter "…by URL" label
// pads out on the right — no wasted space on the wide ones.
const ADD_BTN_WIDTH = "w-[170px]";

export function HeaderAddButton({
  label,
  onClick,
  testid,
  title,
}: {
  label: string;
  onClick: () => void;
  testid: string;
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      data-testid={testid}
      title={title}
      className={
        `inline-flex h-[30px] ${ADD_BTN_WIDTH} shrink-0 items-center gap-1.5 rounded-7 ` +
        "border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
      }
    >
      <Icon name="plus" size={14} strokeWidth={2} />
      {label}
    </button>
  );
}

// Same alignment idea for the "Deleted …" buttons that sit just left of the
// add button in all three headers, but sized by an INVISIBLE copy of the
// longest label ("Deleted Applications") instead of a hand-measured pixel
// constant — the longest button is exactly its natural size, the shorter two
// match it, and nothing drifts if the font changes. The count badge floats on
// the corner so it never adds width.
export function HeaderDeletedButton({
  label,
  count,
  onClick,
  testid,
}: {
  label: string;
  count: number;
  onClick: () => void;
  testid: string;
}) {
  const { t } = useTranslation();
  return (
    <button
      onClick={onClick}
      data-testid={testid}
      className="relative inline-flex h-[30px] shrink-0 items-center rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink"
    >
      <span aria-hidden="true" className="invisible flex items-center gap-1.5 whitespace-nowrap">
        <Icon name="trash" size={14} strokeWidth={2} />
        {t("shell.deletedSizerLabel")}
      </span>
      <span className="absolute inset-y-0 left-3 flex items-center gap-1.5 whitespace-nowrap">
        <Icon name="trash" size={14} strokeWidth={2} />
        {label}
      </span>
      {count > 0 ? (
        <span className="absolute -right-1.5 -top-1.5 inline-flex h-[16px] min-w-[16px] items-center justify-center rounded-full bg-bad px-1 font-mono text-[10px] font-bold text-white">
          {count}
        </span>
      ) : null}
    </button>
  );
}
