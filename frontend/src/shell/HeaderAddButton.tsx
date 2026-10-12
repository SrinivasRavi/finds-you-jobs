// The accent "+ Add …" action that sits at the right end of the Job Board,
// Applications, and Networking headers. 1 shared width + left-aligned content
// keeps the "＋ Add a " prefix aligned when you switch tabs. The width is the
// longest of the 3 labels in the current language, from an invisible copy of
// each, so a longer translation widens all 3 instead of overflowing.

import { useTranslation } from "react-i18next";

import { Icon } from "./icons";

const ADD_LABELS = ["jobBoard.header.addJob", "tracker.addApplication", "networking.addByUrl"];

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
  const { t } = useTranslation();
  return (
    <button
      onClick={onClick}
      data-testid={testid}
      title={title}
      className={
        "relative inline-flex h-[30px] shrink-0 items-center rounded-7 " +
        "border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
      }
    >
      <span aria-hidden="true" className="invisible grid">
        {ADD_LABELS.map((k) => (
          <span key={k} className="col-start-1 row-start-1 flex items-center gap-1.5 whitespace-nowrap">
            <Icon name="plus" size={14} strokeWidth={2} />
            {t(k)}
          </span>
        ))}
      </span>
      <span className="absolute inset-y-0 left-3 flex items-center gap-1.5 whitespace-nowrap">
        <Icon name="plus" size={14} strokeWidth={2} />
        {label}
      </span>
    </button>
  );
}

// Same alignment idea for the "Deleted …" buttons that sit just left of the
// add button in all three headers, but sized by an INVISIBLE copy of the
// longest label ("Deleted Applications") instead of a hand-measured pixel
// constant — the longest button is exactly its natural size, the shorter two
// match it, and nothing drifts if the font changes. The count reads as a quiet
// "(N)" suffix, not a notification badge (maintainer 2026-07-24). The sizer
// pins to the LABEL alone (no reserved-count digits): reserving " (99)" left a
// visibly unbalanced right gap on the longest tab (maintainer 2026-07-28). The
// count still can't shift the box — the visible run is absolutely positioned,
// so its width is decoupled from the button's — it just fills the label's own
// slack on the shorter tabs.
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
        {count > 0 ? `${label} (${count})` : label}
      </span>
    </button>
  );
}
