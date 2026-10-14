// One locale-aware date/time formatter for the whole app (duplication audit
// D-F4). Three hand-rolled formatters had drifted apart — one of them pinned to
// "en-GB" in a 13-language product — so every displayed timestamp now resolves
// its locale here.

import i18n from "../i18n";

/** The BCP-47 tag `Intl` formats with, or undefined for the OS locale. English
 *  defers to the OS, so a UK install still reads 02/08/2026; a chosen non-English
 *  UI formats in that language (i18next holds only the 13 short codes). */
function appLocale(): string | undefined {
  const lng = i18n.language;
  return !lng || lng === "en" ? undefined : lng;
}

export type WhenStyle =
  /** Date only — the opt-in "acknowledged on" line. */
  | "date"
  /** Date + time in the runtime's own shape — LinkedIn session expiry. */
  | "dateTime"
  /** Date + hh:mm:ss — the operations-log table. */
  | "timestamp"
  /** Spelled-out date — the application activity timeline. */
  | "longDate";

/** Format an ISO timestamp for display. Returns `fallback` when the value is
 *  missing or unparseable, so no surface can print "Invalid Date". */
export function formatWhen(
  iso: string | null | undefined,
  style: WhenStyle = "dateTime",
  fallback = "—",
): string {
  if (!iso) return fallback;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return fallback;
  const loc = appLocale();
  switch (style) {
    case "date":
      return d.toLocaleDateString(loc);
    case "timestamp":
      return `${d.toLocaleDateString(loc)} ${d.toLocaleTimeString(loc, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })}`;
    case "longDate":
      return d.toLocaleDateString(loc, { day: "numeric", month: "long", year: "numeric" });
    default:
      return d.toLocaleString(loc);
  }
}
