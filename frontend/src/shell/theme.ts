// Theme state — ports the prototype's `data-theme` + localStorage("fyj-theme")
// mechanism (head.js / shell.js), extended with a "Follow system" mode
// (FR-SET-09): the stored value is the user's *mode* (light | dark | system);
// "system" resolves through `prefers-color-scheme` and tracks OS changes live
// via a matchMedia listener. A persisted light/dark choice always wins.

import { useSyncExternalStore } from "react";

/** The resolved theme applied to the DOM (`data-theme`). */
export type Theme = "light" | "dark";
/** The user's choice — "system" follows `prefers-color-scheme` (default). */
export type ThemeMode = "light" | "dark" | "system";
const KEY = "fyj-theme";

function readMode(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "system";
  } catch {
    return "system";
  }
}

function systemTheme(): Theme {
  try {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

/** The resolved theme for a mode (system → the OS preference). */
function resolve(mode: ThemeMode): Theme {
  return mode === "system" ? systemTheme() : mode;
}

function apply(t: Theme): void {
  document.documentElement.setAttribute("data-theme", t);
}

const listeners = new Set<() => void>();
function notify(): void {
  for (const fn of listeners) fn();
}
function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// Live OS-theme tracking: while the user is in "system" mode, an OS light/dark
// switch re-applies + notifies. Registered once at module load; a no-op where
// matchMedia is unavailable (older jsdom / SSR).
try {
  const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
  mq?.addEventListener?.("change", () => {
    if (readMode() === "system") {
      apply(systemTheme());
      notify();
    }
  });
} catch {
  /* no matchMedia — follow-system tracking simply degrades to the last apply */
}

/** Persist the mode and apply its resolved theme immediately. */
export function setThemeMode(mode: ThemeMode): void {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* ignore */
  }
  apply(resolve(mode));
  notify();
}

/** `[mode, resolvedTheme, setMode]` — mode is the user's choice, resolvedTheme
 *  is what's on the DOM (system resolved to light/dark). */
export function useThemeMode(): [ThemeMode, Theme, (mode: ThemeMode) => void] {
  const mode = useSyncExternalStore(subscribe, readMode, () => "system" as ThemeMode);
  return [mode, resolve(mode), setThemeMode];
}
