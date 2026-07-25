// Appearance pane (theme + language). Untitled card — the pane header already
// says "Appearance" (maintainer 2026-07-24 #5: stop repeating it). (Extracted
// from Settings.tsx 2026-07-25, F-M6 monolith split — pure moves, zero
// behavior change; the useThemeMode hook moved in with it — it is a
// useSyncExternalStore over localStorage, so relocating the subscriber changes
// nothing.) Memoized (no props).

import { memo } from "react";
import { useTranslation } from "react-i18next";

import { LanguageSelect } from "../../shell/LanguageSelect";
import { type ThemeMode, useThemeMode } from "../../shell/theme";
import { Section } from "./shared";

// Three-way theme selector (FR-SET-09): Light / Dark / Follow system. The
// persisted mode wins; "system" resolves through prefers-color-scheme live.
const THEME_MODES: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "appearance.light" },
  { value: "dark", label: "appearance.dark" },
  { value: "system", label: "appearance.system" },
];

function ThemeModeToggle({
  mode,
  onChange,
}: {
  mode: ThemeMode;
  onChange: (m: ThemeMode) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="inline-flex overflow-hidden rounded-7 border border-border text-[12px]">
      {THEME_MODES.map((m) => (
        <button
          key={m.value}
          data-testid={`theme-mode-${m.value}`}
          aria-pressed={mode === m.value}
          onClick={() => onChange(m.value)}
          className={
            "px-2.5 py-1 " +
            (mode === m.value ? "bg-accent text-white" : "bg-surface text-ink-2 hover:bg-surface-3")
          }
        >
          {t(m.label)}
        </button>
      ))}
    </div>
  );
}

export const AppearanceSection = memo(function AppearanceSection() {
  const { t } = useTranslation();
  const [themeMode, , setThemeMode] = useThemeMode();
  return (
    <Section>
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="text-[13px] font-medium text-ink">{t("appearance.theme")}</div>
            <div className="text-[11.5px] text-ink-3">{t("appearance.themeHint")}</div>
          </div>
          <ThemeModeToggle mode={themeMode} onChange={setThemeMode} />
        </div>
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="text-[13px] font-medium text-ink">{t("appearance.language")}</div>
            <div className="text-[11.5px] text-ink-3">{t("appearance.languageHint")}</div>
          </div>
          <LanguageSelect testid="language-select" />
        </div>
      </div>
    </Section>
  );
});
