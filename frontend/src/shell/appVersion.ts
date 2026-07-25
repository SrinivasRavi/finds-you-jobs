// App version display. The packaged desktop app reads its true bundle version
// through Tauri's getVersion() (sourced from tauri.conf.json). The browser-dev
// and test paths have no Tauri, so they fall back to __APP_VERSION__ — injected
// from frontend/package.json at build time (vite `define`).
//
// Both sources are strictly numeric ("0.5.4"): the embedded bundle version must
// stay numeric because WiX/MSI rejects non-numeric pre-release suffixes
// (distribution.md §2a). Every pre-1.0 build is distributed as a "-beta"
// (the suffix lives only in the release tag + installer filenames), so we append
// it here for display. Flip RELEASE_CHANNEL_SUFFIX to "" when the first stable
// (1.0) release is cut.

import { useEffect, useState } from "react";

declare const __APP_VERSION__: string;

export const RELEASE_CHANNEL_SUFFIX = "-beta";

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** Raw numeric version, e.g. "0.5.4". */
export async function rawAppVersion(): Promise<string> {
  if (inTauri()) {
    try {
      const { getVersion } = await import("@tauri-apps/api/app");
      return await getVersion();
    } catch {
      /* fall through to the build-time constant */
    }
  }
  return __APP_VERSION__;
}

/** Display version, e.g. "v0.5.4-beta". */
export function formatVersion(raw: string): string {
  return `v${raw}${RELEASE_CHANNEL_SUFFIX}`;
}

/** Resolves the display version asynchronously; null until it arrives. */
export function useAppVersion(): string | null {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void rawAppVersion().then((raw) => {
      if (alive) setVersion(formatVersion(raw));
    });
    return () => {
      alive = false;
    };
  }, []);
  return version;
}
