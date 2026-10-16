// OS-style Settings navigation: a category rail and 1 pane per category, like
// macOS or Windows Settings. Categories follow the features (url-less-jobs.md
// section 11.5); nothing persists the active one, so renaming ids breaks nothing.
// Memoized: `active` is a primitive, `onPick` a stable setState.

import { memo } from "react";
import { useTranslation } from "react-i18next";

import { Icon, type IconName } from "../../shell/icons";

export type SettingsCat =
  | "providers"
  | "discovery"
  | "scoring"
  | "documents"
  | "applying"
  | "networking"
  | "autopilot"
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
  { id: "discovery", label: "settingsNav.discovery", icon: "search", blurb: "settingsNav.discoveryBlurb" },
  { id: "scoring", label: "settingsNav.scoring", icon: "barChart", blurb: "settingsNav.scoringBlurb" },
  { id: "documents", label: "settingsNav.documents", icon: "file", blurb: "settingsNav.documentsBlurb" },
  { id: "applying", label: "settingsNav.applying", icon: "check", blurb: "settingsNav.applyingBlurb" },
  { id: "networking", label: "settingsNav.networking", icon: "share", blurb: "settingsNav.networkingBlurb" },
  { id: "autopilot", label: "settingsNav.autopilot", icon: "refreshCw", blurb: "settingsNav.autopilotBlurb" },
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
