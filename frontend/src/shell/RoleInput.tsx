// The role field: a ChipInput that offers job titles as you type (10-10 report
// 4.3). Suggestions only: any text is a role, and Enter keeps what was typed.

import { useState } from "react";

import { useTitleSuggestions } from "../api/queries";
import { ChipInput } from "./ChipInput";
import { useDebounced } from "./useDebounced";

export function RoleInput(
  props: Omit<Parameters<typeof ChipInput>[0], "suggestions">,
) {
  const [draft, setDraft] = useState("");
  const query = useDebounced(draft.trim(), 150);
  const { data } = useTitleSuggestions(query);
  const fresh = query && query.toLowerCase() === draft.trim().toLowerCase() ? (data ?? []) : [];
  return (
    <ChipInput
      {...props}
      suggestions={fresh.filter((s) => !props.items.includes(s))}
      onDraftChange={(v) => {
        setDraft(v);
        props.onDraftChange?.(v);
      }}
    />
  );
}
