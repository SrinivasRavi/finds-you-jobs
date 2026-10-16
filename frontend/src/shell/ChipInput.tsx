// The one chip editor for list-valued preferences (duplication audit D-F10).
// Onboarding and the Job finder preferences modal each carried their own copy
// over the same fields (role aliases, locations), and their key handling had
// already drifted. The add/dedupe/keyboard rules are shared here; the two
// surfaces' chrome genuinely differs, so that is a `variant`.

import { useState } from "react";
import { useTranslation } from "react-i18next";

export type ChipInputVariant =
  /** Job finder preferences: section heading + one bordered field holding the
   *  chips and a borderless text input. */
  | "boxed"
  /** Onboarding: plain label + free-floating accent chips beside a bordered
   *  text input. */
  | "plain";

export function ChipInput({
  label,
  hint,
  items,
  onAdd,
  onRemove,
  placeholder,
  testid,
  variant = "boxed",
  onDraftChange,
  suggestions,
}: {
  label: string;
  hint?: string;
  items: string[];
  onAdd: (v: string) => void;
  onRemove: (v: string) => void;
  placeholder: string;
  /** Container testid; the text field carries `${testid}-input`. */
  testid: string;
  variant?: ChipInputVariant;
  /** The uncommitted text, so a parent can treat a typed entry as given. */
  onDraftChange?: (v: string) => void;
  /** Offered for the current text. Only an arrow-picked or clicked one is added:
   *  Enter keeps what was typed, so a suggestion never overrides the user. */
  suggestions?: string[];
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const offered = open && input.trim().length >= 2 ? (suggestions ?? []) : [];

  // Enter and comma both commit, and the field swallows them either way — even
  // on empty text. Job finder preferences renders inside a <form>, where a bare
  // Enter used to fall through and submit it ("Rescan now" + close the modal).
  // Leaving the field commits too, so a typed value is never dropped (S-C58).
  function commit(picked?: string) {
    const v = (picked ?? input).trim();
    if (v && !items.includes(v)) onAdd(v);
    setInput("");
    setActive(-1);
    onDraftChange?.("");
  }

  const chips = items.map((it) => (
    <span
      key={it}
      className={
        variant === "boxed"
          ? "inline-flex items-center gap-1 rounded-full border border-border-2 bg-surface-2 px-2 py-0.5 text-[12px] text-ink"
          : "inline-flex items-center gap-1 rounded-full bg-accent-wash px-2 py-0.5 text-[11.5px] text-accent-ink"
      }
    >
      {it}
      <button
        type="button"
        onClick={() => onRemove(it)}
        aria-label={
          variant === "boxed"
            ? t("jobBoard.prefs.removeAria")
            : t("onboarding.removeChip", { value: it })
        }
        className={variant === "boxed" ? "text-ink-3 hover:text-bad" : "text-accent-ink/70"}
      >
        ×
      </button>
    </span>
  ));

  const field = (
    <div className={variant === "boxed" ? "relative min-w-[120px] flex-1" : "relative min-w-[160px] flex-1"}>
      <input
        value={input}
        data-testid={`${testid}-input`}
        role={suggestions ? "combobox" : undefined}
        aria-expanded={suggestions ? offered.length > 0 : undefined}
        onChange={(e) => {
          setInput(e.target.value);
          setActive(-1);
          setOpen(true);
          onDraftChange?.(e.target.value);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          setOpen(false);
          commit();
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && offered.length) {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, offered.length - 1));
          } else if (e.key === "ArrowUp" && offered.length) {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, -1));
          } else if (e.key === "Escape") {
            setOpen(false);
          } else if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit(active >= 0 ? offered[active] : undefined);
          }
        }}
        placeholder={placeholder}
        className={
          variant === "boxed"
            ? "w-full bg-transparent text-[12.5px] text-ink placeholder:text-ink-4 focus:outline-none"
            : "w-full rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
        }
      />
      {offered.length ? (
        <ul
          role="listbox"
          data-testid={`${testid}-suggestions`}
          className="absolute left-0 top-full z-30 mt-1 max-h-[260px] w-[min(360px,80vw)] overflow-y-auto rounded-md border border-border-2 bg-surface py-1 shadow-lg"
        >
          {offered.map((s, i) => (
            <li
              key={s}
              role="option"
              aria-selected={i === active}
              data-testid={`${testid}-option`}
              // mousedown, not click: a click lands after the input's blur.
              onMouseDown={(e) => {
                e.preventDefault();
                commit(s);
              }}
              onMouseEnter={() => setActive(i)}
              className={`cursor-pointer px-3 py-1.5 text-[12.5px] ${
                i === active ? "bg-accent-wash text-accent-ink" : "text-ink"
              }`}
            >
              {s}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );

  if (variant === "plain") {
    return (
      <div>
        <div className="mb-1 text-[12px] text-ink-3">{label}</div>
        {hint ? <div className="mb-1.5 text-[11px] text-ink-4">{hint}</div> : null}
        <div data-testid={testid} className="flex flex-wrap gap-1.5">
          {chips}
          {field}
        </div>
      </div>
    );
  }

  return (
    <section className="space-y-2">
      <header>
        <h3 className="text-[13px] font-semibold text-ink">{label}</h3>
        <p className="text-[11.5px] text-ink-3">{hint}</p>
      </header>
      <div
        data-testid={testid}
        className="flex min-h-[36px] flex-wrap items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-2 py-1.5 focus-within:border-accent"
      >
        {chips}
        {field}
      </div>
    </section>
  );
}
