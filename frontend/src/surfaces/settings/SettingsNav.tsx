// OS-style Settings navigation (maintainer directive 2026-07-23): a left
// category rail + one focused pane per category, the way macOS/Windows Settings
// organize a large surface — instead of one long scroll where "Prompts" was
// invisible at the bottom. Each pane reuses the existing `Section` cards.
// (Extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure moves,
// zero behavior change.) Memoized: `active` is a primitive, `onPick` the
// root's stable setState.

import { memo } from "react";
import { useTranslation } from "react-i18next";

import { Icon, type IconName } from "../../shell/icons";

export type SettingsCat =
  | "providers"
  | "prompts"
  | "discovery"
  | "networking"
  | "data"
  | "appearance"
  | "about";

export const SETTINGS_CATS: {
  id: SettingsCat;
  label: string; // i18n key
  icon: IconName;
  blurb: string; // i18n key
}[] = [
  { id: "providers", label: "settingsNav.providers", icon: "settings", blurb: "settingsNav.providersBlurb" },
  { id: "prompts", label: "settingsNav.prompts", icon: "pencil", blurb: "settingsNav.promptsBlurb" },
  { id: "discovery", label: "settingsNav.discovery", icon: "search", blurb: "settingsNav.discoveryBlurb" },
  { id: "networking", label: "settingsNav.networking", icon: "share", blurb: "settingsNav.networkingBlurb" },
  { id: "data", label: "settingsNav.data", icon: "barChart", blurb: "settingsNav.dataBlurb" },
  { id: "appearance", label: "settingsNav.appearance", icon: "sun", blurb: "settingsNav.appearanceBlurb" },
  { id: "about", label: "settingsNav.about", icon: "info", blurb: "settingsNav.aboutBlurb" },
];

export const SettingsNav = memo(function SettingsNav({
  active,
  onPick,
}: {
  active: SettingsCat;
  onPick: (c: SettingsCat) => void;
}) {
  const { t } = useTranslation();
  return (
    <nav
      aria-label={t("settingsPage.navAriaLabel")}
      data-testid="settings-nav"
      className="w-56 shrink-0 space-y-0.5 overflow-y-auto border-r border-border bg-surface p-3"
    >
      {SETTINGS_CATS.map((c) => {
        const on = c.id === active;
        return (
          <button
            key={c.id}
            type="button"
            onClick={() => onPick(c.id)}
            data-testid={`settings-nav-${c.id}`}
            aria-current={on ? "page" : undefined}
            className={
              "flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors " +
              (on ? "bg-accent-wash text-accent-ink" : "text-ink-2 hover:bg-surface-3")
            }
          >
            <span className={"mt-0.5 " + (on ? "text-accent" : "text-ink-3")}>
              <Icon name={c.icon} size={15} strokeWidth={2} />
            </span>
            <span className="min-w-0">
              <span className="block text-[13px] font-medium leading-tight">{t(c.label)}</span>
              <span className="block truncate text-[11px] leading-tight text-ink-4">
                {t(c.blurb)}
              </span>
            </span>
          </button>
        );
      })}
    </nav>
  );
});
