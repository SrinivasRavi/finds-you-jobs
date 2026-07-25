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
// Lazy locales (F-L10, 2026-07-25): only English (the fallback) ships in the
// boot chunk; the other 12 languages are code-split via dynamic import() and
// loaded on demand. Still "bundled locally" — the chunks are app assets served
// from the same origin, so the loopback-only CSP holds. The persisted choice
// is awaited at module scope (top-level await) BEFORE init, so a non-English
// boot never flashes English: main.tsx's side-effect import doesn't resolve —
// and React doesn't render — until the boot locale is registered.
//
// Arabic renders right-to-left: the <html dir> attribute follows the active
// language (flex layouts mirror automatically; full RTL polish of the few
// absolutely-positioned elements is tracked as follow-up work).

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

// Monotonic switch token: each setLanguage call claims a new token, and only
// the latest call applies. This must NOT lean on readLanguage() — if
// localStorage.setItem throws (persistence is best-effort), a storage-based
// guard would never match and in-session switching would go dead, and an
// unconditional applyDirection could flip the page RTL while the language
// itself never changed.
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
