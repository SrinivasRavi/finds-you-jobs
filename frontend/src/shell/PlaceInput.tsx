// The location picker (docs/internal/place-picker.md section 3). Typing lists
// places named in full; picking one stores the place itself, so the scorer never
// guesses which London. Enter, Tab or leaving the box takes the top suggestion
// and shows its full name; text matching no place is kept as typed. A comma
// never commits, because full names contain commas.

import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api";
import { usePlaceSuggestions } from "../api/queries";
import type { PlaceChip, PlaceSuggestion } from "../api/types";
import type { ChipInputVariant } from "./ChipInput";
import { useDebounced } from "./useDebounced";

export const placeLabel = (chip: PlaceChip): string =>
  typeof chip === "string" ? chip : chip.label;

export function PlaceInput({
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
  items: PlaceChip[];
  onAdd: (v: PlaceChip) => void;
  onRemove: (v: PlaceChip) => void;
  placeholder: string;
  /** Container testid; the text field carries `${testid}-input`. */
  testid: string;
  variant?: ChipInputVariant;
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const query = useDebounced(input.trim(), 150);
  const { data } = usePlaceSuggestions(query);
  const qc = useQueryClient();
  // Only suggestions for what's in the box now; a slow answer for an older
  // prefix must never be what Enter picks.
  const suggestions: PlaceSuggestion[] =
    query && query.toLowerCase() === input.trim().toLowerCase() ? (data ?? []) : [];

  function add(chip: PlaceChip) {
    const key = placeLabel(chip);
    if (key && !items.some((it) => placeLabel(it) === key)) onAdd(chip);
    setInput("");
    setActive(0);
    setOpen(false);
  }

  /** The top (or highlighted) suggestion, else the text as typed. Enter can
   *  beat the debounce, so a stale list is fetched fresh for exactly this text.
   *  While that fetch is out, a 2nd commit of the same text (Enter, then leaving
   *  the box) is the same commit, so it adds nothing. */
  const pending = useRef<string | null>(null);
  async function commit() {
    const text = input.trim();
    if (!text || pending.current === text) return;
    if (suggestions.length || text.length < 2) {
      add(suggestions[active] ?? suggestions[0] ?? text);
      return;
    }
    pending.current = text;
    try {
      const fresh = await qc
        .fetchQuery({
          queryKey: ["placeSuggest", text.toLowerCase()],
          queryFn: () => api.suggestPlaces(text),
          staleTime: Infinity,
        })
        .catch(() => [] as PlaceSuggestion[]);
      add(fresh[0] ?? text);
    } finally {
      pending.current = null;
    }
  }

  const chips = items.map((it) => {
    const name = placeLabel(it);
    return (
      <span
        key={name}
        className={
          variant === "boxed"
            ? "inline-flex items-center gap-1 rounded-full border border-border-2 bg-surface-2 px-2 py-0.5 text-[12px] text-ink"
            : "inline-flex items-center gap-1 rounded-full bg-accent-wash px-2 py-0.5 text-[11.5px] text-accent-ink"
        }
      >
        {name}
        <button
          type="button"
          onClick={() => onRemove(it)}
          aria-label={t("onboarding.removeChip", { value: name })}
          className={variant === "boxed" ? "text-ink-3 hover:text-bad" : "text-accent-ink/70"}
        >
          ×
        </button>
      </span>
    );
  });

  const showList = open && input.trim().length >= 2;
  const field = (
    <div className={variant === "boxed" ? "relative min-w-[160px] flex-1" : "relative min-w-[200px] flex-1"}>
      <input
        value={input}
        data-testid={`${testid}-input`}
        role="combobox"
        aria-expanded={showList}
        aria-autocomplete="list"
        onChange={(e) => {
          setInput(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => void commit()}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && suggestions.length) {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, suggestions.length - 1));
          } else if (e.key === "ArrowUp" && suggestions.length) {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter") {
            // Swallowed even on empty text: the preferences dialog is a form,
            // and a bare Enter used to submit it.
            e.preventDefault();
            void commit();
          } else if (e.key === "Tab" && input.trim()) {
            void commit();
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
        placeholder={placeholder}
        className={
          variant === "boxed"
            ? "w-full bg-transparent text-[12.5px] text-ink placeholder:text-ink-4 focus:outline-none"
            : "w-full rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
        }
      />
      {showList ? (
        <ul
          role="listbox"
          aria-label={t("jobBoard.prefs.placeListAria")}
          data-testid={`${testid}-suggestions`}
          className="absolute left-0 top-full z-30 mt-1 max-h-[260px] w-[min(420px,80vw)] overflow-y-auto rounded-md border border-border-2 bg-surface py-1 shadow-lg"
        >
          {suggestions.length === 0 ? (
            <li className="px-3 py-1.5 text-[12px] text-ink-3">
              {t("jobBoard.prefs.placeNoMatch", { text: input.trim() })}
            </li>
          ) : (
            suggestions.map((s, i) => (
              <li
                key={s.label}
                role="option"
                aria-selected={i === active}
                data-testid={`${testid}-option`}
                // mousedown, not click: a click lands after the input's blur,
                // which would already have committed the top suggestion.
                onMouseDown={(e) => {
                  e.preventDefault();
                  add(s);
                }}
                onMouseEnter={() => setActive(i)}
                className={`cursor-pointer px-3 py-1.5 text-[12.5px] ${
                  i === active ? "bg-accent-wash text-accent-ink" : "text-ink"
                }`}
              >
                {s.label}
              </li>
            ))
          )}
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
