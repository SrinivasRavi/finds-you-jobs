// The language picker — one shared control for onboarding (top of the wizard)
// and Settings → Appearance. Options show each language's own name (endonym).

import { useTranslation } from "react-i18next";

import { LANGUAGES, readLanguage, setLanguage, type LanguageCode } from "../i18n";

export function LanguageSelect({ testid, className }: { testid: string; className?: string }) {
  // Subscribes this control to language changes so `value` tracks the switch.
  useTranslation();
  return (
    <select
      value={readLanguage()}
      onChange={(e) => setLanguage(e.target.value as LanguageCode)}
      data-testid={testid}
      aria-label="Language"
      className={
        className ??
        "rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink"
      }
    >
      {LANGUAGES.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  );
}
