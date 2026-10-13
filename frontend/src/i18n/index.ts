// i18n: i18next with every locale bundled, so the loopback-only CSP never fetches.
// The choice persists in localStorage ("fyj-language") and applies without a reload.
// English ships in the boot chunk and the 12 others load on demand (F-L10); the
// saved choice is awaited before init, so a non-English boot never flashes English.
// Arabic sets <html dir="rtl">.

import i18n, { type Resource } from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en";
import type { DeepPartial, Messages } from "./locales/types";

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

// One dynamic-import loader per non-English language — Vite splits each into
// its own chunk, fetched only when that language is selected.
const LOADERS: Record<
  Exclude<LanguageCode, "en">,
  () => Promise<{ default: DeepPartial<Messages> }>
> = {
  ar: () => import("./locales/ar"),
  de: () => import("./locales/de"),
  es: () => import("./locales/es"),
  fr: () => import("./locales/fr"),
  hi: () => import("./locales/hi"),
  it: () => import("./locales/it"),
  ja: () => import("./locales/ja"),
  nl: () => import("./locales/nl"),
  pl: () => import("./locales/pl"),
  pt: () => import("./locales/pt"),
  ru: () => import("./locales/ru"),
  zh: () => import("./locales/zh"),
};

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

/** Fetch + register a language's chunk (no-op for en / already-loaded). */
async function loadLocale(code: LanguageCode): Promise<void> {
  if (code === "en" || i18n.hasResourceBundle(code, "translation")) return;
  const mod = await LOADERS[code]();
  i18n.addResourceBundle(code, "translation", mod.default, true, true);
}

// Monotonic switch token: only the latest setLanguage call applies. It can't
// lean on readLanguage(): storage writes are best-effort, so a storage guard could
// go dead or flip the page RTL while the language never changed.
let switchToken = 0;

/** Persist the language and switch the live UI (text + direction) to it. */
export function setLanguage(code: LanguageCode): void {
  const token = ++switchToken;
  try {
    localStorage.setItem(KEY, code);
  } catch {
    /* ignore — persistence is best-effort; the live switch still happens */
  }
  void loadLocale(code)
    .catch(() => {
      /* chunk failed to load — changeLanguage still runs; missing strings
         render English via fallbackLng, matching the old bundled behavior
         for a partially translated locale. */
    })
    .then(() => {
      // Guard a fast double-switch: only the latest call applies. Direction
      // flips together with the text so a superseded or failed switch never
      // leaves dir/lang pointing at a language the UI isn't showing.
      if (token !== switchToken) return;
      applyDirection(code);
      void i18n.changeLanguage(code);
    });
}

// Boot: register en (fallback, always eager) plus the persisted language's
// bundle — awaited here so init below stays synchronous with everything the
// first paint needs already in memory.
const bootLanguage = readLanguage();
const bootResources: Resource = { en: { translation: en } };
if (bootLanguage !== "en") {
  try {
    bootResources[bootLanguage] = {
      translation: (await LOADERS[bootLanguage]()).default,
    };
  } catch {
    /* chunk failed — boot renders English via fallbackLng */
  }
}

void i18n.use(initReactI18next).init({
  resources: bootResources,
  lng: bootLanguage,
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React already escapes
  // The boot resources are in memory by now — init synchronously so the very
  // first React render never races the init microtask, and never suspend: a
  // not-yet-ready i18n must render fallback text, not suspend the tree (no
  // Suspense boundary exists; the race showed up as intermittent blank first
  // paints in e2e, 2026-07-24).
  initAsync: false,
  react: { useSuspense: false },
});
applyDirection(bootLanguage);

export default i18n;
