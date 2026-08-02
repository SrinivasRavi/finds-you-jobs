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
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");

  // Enter and comma both commit, and the field swallows them either way — even
  // on empty text. Job finder preferences renders inside a <form>, where a bare
  // Enter used to fall through and submit it ("Rescan now" + close the modal).
  function commit() {
    const v = input.trim();
    if (v && !items.includes(v)) onAdd(v);
    setInput("");
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
    <input
      value={input}
      data-testid={`${testid}-input`}
      onChange={(e) => setInput(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === ",") {
          e.preventDefault();
          commit();
        }
      }}
      placeholder={placeholder}
      className={
        variant === "boxed"
          ? "min-w-[120px] flex-1 bg-transparent text-[12.5px] text-ink placeholder:text-ink-4 focus:outline-none"
          : "min-w-[160px] flex-1 rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
      }
    />
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
