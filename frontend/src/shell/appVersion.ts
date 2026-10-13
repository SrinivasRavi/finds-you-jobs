// App version. The packaged app reads its bundle version from Tauri; dev and
// tests fall back to __APP_VERSION__ from frontend/package.json. Both are numeric,
// because WiX/MSI rejects suffixes (distribution.md section 2a), so the "-beta"
// every pre-1.0 build ships as is appended here. Set RELEASE_CHANNEL_SUFFIX to ""
// at 1.0.

import { useEffect, useState } from "react";

import { inTauri } from "./host";

declare const __APP_VERSION__: string;

export const RELEASE_CHANNEL_SUFFIX = "-beta";

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
