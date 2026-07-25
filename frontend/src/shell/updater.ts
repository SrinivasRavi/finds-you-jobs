// In-app software update — a thin wrapper over tauri-plugin-updater.
//
// The check + download + install all run natively in the Rust core (not the
// webview), so there is no CSP or network config to manage on this side; the
// single allowed update endpoint is declared in tauri.conf.json's
// `plugins.updater` and every downloaded update is Ed25519-verified against the
// baked-in public key before it is applied. Everything here degrades gracefully
// in the browser-dev path (no Tauri): `updaterAvailable()` is false and the
// About panel shows the update controls as unavailable rather than erroring.
//
// Data safety: an update replaces the application binary only. The user's
// profile, applications, resumes, and keys live in the OS data dir
// (resolve_data_dir), untouched by an update.

import { useSyncExternalStore } from "react";

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** True only inside the packaged desktop app, where the updater plugin exists. */
export function updaterAvailable(): boolean {
  return inTauri();
}

export type UpdateHandle = {
  version: string;
  /** Release notes from the manifest, if any. */
  notes?: string;
  /** Download + install, then relaunch. `onProgress` gets 0..1, or null when
   *  the total size is unknown. Resolves just before the app relaunches. */
  install: (onProgress?: (fraction: number | null) => void) => Promise<void>;
};

export type CheckResult = { available: false } | ({ available: true } & UpdateHandle);

/** Ask the configured endpoint whether a newer signed release exists. */
export async function checkForUpdate(): Promise<CheckResult> {
  const { check } = await import("@tauri-apps/plugin-updater");
  const update = await check();
  if (!update) return { available: false };
  return {
    available: true,
    version: update.version,
    notes: update.body || undefined,
    install: async (onProgress) => {
      let total = 0;
      let received = 0;
      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? 0;
          received = 0;
        } else if (event.event === "Progress") {
          received += event.data.chunkLength;
          onProgress?.(total > 0 ? received / total : null);
        } else if (event.event === "Finished") {
          onProgress?.(1);
        }
      });
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    },
  };
}

// --- "Check on launch" preference (client-only, default off) -----------------
// Kept in localStorage rather than the sidecar settings so it needs no schema
// migration and works before the backend is even ready. Off by default: an
// update check is an outbound request to GitHub, and finds-you-jobs makes no
// silent network calls the user did not turn on (vision: no telemetry, control
// over what leaves the machine).

const AUTO_KEY = "fyj-auto-update-check";
const autoListeners = new Set<() => void>();

function readAuto(): boolean {
  try {
    return localStorage.getItem(AUTO_KEY) === "1";
  } catch {
    return false;
  }
}

export function setAutoUpdateCheck(on: boolean): void {
  try {
    localStorage.setItem(AUTO_KEY, on ? "1" : "0");
  } catch {
    /* ignore — a private-mode / disabled storage just means it won't persist */
  }
  for (const fn of autoListeners) fn();
}

function subscribeAuto(fn: () => void): () => void {
  autoListeners.add(fn);
  return () => autoListeners.delete(fn);
}

/** `[enabled, setEnabled]` for the "check for updates on launch" toggle. */
export function useAutoUpdateCheck(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(subscribeAuto, readAuto, () => false);
  return [on, setAutoUpdateCheck];
}

export function autoUpdateCheckEnabled(): boolean {
  return readAuto();
}
