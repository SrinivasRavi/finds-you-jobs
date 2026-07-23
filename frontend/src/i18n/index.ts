// i18n — i18next + react-i18next (both MIT), all locales bundled locally so
// the packaged app's loopback-only CSP never fetches anything. The selected
// language persists in localStorage ("fyj-language", mirroring "fyj-theme")
// and applies immediately via i18next's change events — no reload.
//
// Coverage (2026-07-24): every user-facing string is externalized; English is
// the reference locale and the 12 other locales carry full-parity machine-
// drafted translations (native-speaker PRs welcome). Anything missing in a
// locale falls back to English. Locale files live in ./locales — one file per
// language; en/ is split per-namespace and assembled by en/index.ts.
//
// Arabic renders right-to-left: the <html dir> attribute follows the active
// language (flex layouts mirror automatically; full RTL polish of the few
// absolutely-positioned elements is tracked as follow-up work).

import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import ar from "./locales/ar";
import de from "./locales/de";
import en from "./locales/en";
import es from "./locales/es";
import fr from "./locales/fr";
import hi from "./locales/hi";
import it from "./locales/it";
import ja from "./locales/ja";
import nl from "./locales/nl";
import pl from "./locales/pl";
import pt from "./locales/pt";
import ru from "./locales/ru";
import zh from "./locales/zh";

const KEY = "fyj-language";

export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "es", label: "Español" },
  { code: "hi", label: "हिन्दी" },
  { code: "fr", label: "Français" },
  { code: "de", label: "Deutsch" },
  { code: "pt", label: "Português" },
  { code: "it", label: "Italiano" },
  { code: "nl", label: "Nederlands" },
  { code: "pl", label: "Polski" },
  { code: "ru", label: "Русский" },
  { code: "ja", label: "日本語" },
  { code: "zh", label: "中文（简体）" },
  { code: "ar", label: "العربية" },
] as const;

export type LanguageCode = (typeof LANGUAGES)[number]["code"];

const RTL_LANGUAGES: ReadonlySet<string> = new Set(["ar"]);

export function readLanguage(): LanguageCode {
  try {
    const v = localStorage.getItem(KEY);
    if (LANGUAGES.some((l) => l.code === v)) return v as LanguageCode;
  } catch {
    /* storage unavailable — default */
  }
  return "en";
}

function applyDirection(code: string): void {
  document.documentElement.dir = RTL_LANGUAGES.has(code) ? "rtl" : "ltr";
  document.documentElement.lang = code;
}

/** Persist the language and switch the live UI (text + direction) to it. */
export function setLanguage(code: LanguageCode): void {
  try {
    localStorage.setItem(KEY, code);
  } catch {
    /* ignore */
  }
  applyDirection(code);
  void i18n.changeLanguage(code);
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    es: { translation: es },
    hi: { translation: hi },
    fr: { translation: fr },
    de: { translation: de },
    pt: { translation: pt },
    it: { translation: it },
    nl: { translation: nl },
    pl: { translation: pl },
    ru: { translation: ru },
    ja: { translation: ja },
    zh: { translation: zh },
    ar: { translation: ar },
  },
  lng: readLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React already escapes
  // All resources are bundled inline — init synchronously so the very first
  // React render never races the init microtask, and never suspend: a not-yet
  // -ready i18n must render fallback text, not suspend the tree (no Suspense
  // boundary exists; the race showed up as intermittent blank first paints in
  // e2e, 2026-07-24).
  initAsync: false,
  react: { useSuspense: false },
});
applyDirection(readLanguage());

export default i18n;
