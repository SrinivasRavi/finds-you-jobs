// Shared filter-row visual language (maintainer 2026-07-22): the Job Board's
// two-row header pattern — a top row for actions that add/remove entities from
// the board, and a second row of view modifiers (labeled chip groups +
// separators + a trailing search) — reused verbatim across Applications and
// Networking so the tabs read as one product.

import type { ReactNode } from "react";

import { Icon } from "./icons";

/** The second-row container: same border/padding as the Job Board filter row.
 *  `children` (the filter groups + search) are right-aligned and separated by
 *  <FilterSep />; an optional `left` node holds context that sits on the left
 *  (the left side is allowed to be empty otherwise). */
export function FilterBar({ children, left }: { children: ReactNode; left?: ReactNode }) {
  return (
    <div
      data-testid="filter-bar"
      className="flex min-h-[45px] flex-wrap items-center gap-2 border-b border-border bg-surface px-5 py-2"
    >
      {left ? <div className="flex items-center gap-2">{left}</div> : null}
      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">{children}</div>
    </div>
  );
}

/** A labeled chip group ("SORT", "STATUS", "PRIORITIES", …). */
export function FilterGroup({
  label,
  children,
  id,
}: {
  label: string;
  children: ReactNode;
  id?: string;
}) {
  return (
    <div className="flex items-center gap-1.5" id={id}>
      <span className="text-[11.5px] uppercase tracking-wider text-ink-4">{label}</span>
      {children}
    </div>
  );
}

/** The "|" separator between filter groups. */
export function FilterSep() {
  return <span className="mx-1 h-4 w-px bg-border-2" />;
}

export function Chip({
  active,
  onClick,
  children,
  testid,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  testid?: string;
}) {
  return (
    <button
      onClick={onClick}
      data-testid={testid}
      className={
        "fchip h-7 rounded-full border px-2.5 text-[11.5px] transition " +
        (active
          ? "border-accent bg-accent text-white"
          : "border-border-2 bg-surface text-ink-2 hover:bg-surface-3")
      }
    >
      {children}
    </button>
  );
}

/** The board search box — identical sizing/behavior in every tab: shrinks to
 *  fit the space the groups leave (down to icon + "Search"), only wraps to its
 *  own line when even that can't fit. */
// Default sizing for tabs whose filter row has room (Applications, Networking):
// a fixed 200 px box, right-aligned. The Job Board's crowded row passes its own
// shrink-to-fit className instead; at the maintainer's window width all three
// land at 200 px, and only the Job Board box shrinks on a narrow window.
export function SearchBox({
  value,
  onChange,
  placeholder = "Search",
  testid,
  className = "w-[176px] shrink-0",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  testid: string;
  className?: string;
}) {
  return (
    <div
      className={
        "flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-2 focus-within:border-accent " +
        className
      }
    >
      <Icon name="search" size={13} strokeWidth={2} className="shrink-0 text-ink-4" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        data-testid={testid}
        className="min-w-0 flex-1 bg-transparent text-[12px] text-ink placeholder:text-ink-4 focus:outline-none"
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange("")}
          data-testid={`${testid}-clear`}
          aria-label="Clear search"
          className="shrink-0 text-ink-3 hover:text-ink"
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
