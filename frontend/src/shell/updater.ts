// Update check — "is a newer release out?", and nothing more.
//
// finds-you-jobs does not install its own updates. This is one HTTP GET against
// the GitHub releases API; a newer tag surfaces a link that opens the release
// page in the user's browser, where they download the installer exactly the way
// they got the app in the first place. That leaves a single trust chain: the OS
// verifies our Developer ID signature when the download is opened, and nothing
// here ever writes over the running binary.
//
// The unattended path (download → verify → swap → relaunch) is deliberately
// absent, along with the Ed25519 updater keypair, the signed .sig artifacts and
// the latest.json manifest that only existed to serve it. See
// docs/internal/auto-update.md for what it takes to bring it back.
//
// The check is manual only: Settings › About, on a button press. There is no
// launch-time check and no preference for one, so the app makes NO outbound
// request the user did not just ask for (vision: no silent network calls). The
// next version's single toggle is "auto update to the latest version", which
// governs installing, and it arrives with that feature.
//
// Everything degrades in the browser-dev path (no Tauri): `updaterAvailable()`
// is false and the About panel shows the controls as unavailable, which also
// keeps the e2e run from making outbound calls.

import { rawAppVersion } from "./appVersion";

const REPO = "SrinivasRavi/finds-you-jobs";
const RELEASES_API = `https://api.github.com/repos/${REPO}/releases?per_page=10`;
/** Fallback target when a release carries no html_url of its own. */
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** True only inside the packaged desktop app, where a version check is meaningful. */
export function updaterAvailable(): boolean {
  return inTauri();
}

export type CheckResult =
  | { available: false }
  | {
      available: true;
      /** Numeric-plus-suffix version as tagged, e.g. "0.5.8-beta". */
      version: string;
      /** Release page to open in the user's browser. */
      url: string;
    };

/** `[major, minor, patch]`, or null for anything that isn't a release tag —
 *  which is how the legacy fixed `latest` manifest tag gets skipped. */
function parseVersion(tag: string): [number, number, number] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(tag.trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

function isNewer(candidate: string, current: string): boolean {
  const a = parseVersion(candidate);
  const b = parseVersion(current);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return false;
}

type ReleaseRow = { tag_name?: string; draft?: boolean; html_url?: string };

/** Ask GitHub whether a release newer than the running build exists. Throws on
 *  a network or API failure so callers can tell "no update" from "couldn't ask". */
export async function checkForUpdate(): Promise<CheckResult> {
  const current = await rawAppVersion();
  const response = await fetch(RELEASES_API, {
    headers: { Accept: "application/vnd.github+json" },
  });
  if (!response.ok) throw new Error(`release check failed: ${response.status}`);
  const rows = (await response.json()) as ReleaseRow[];

  let best: { version: string; url: string } | null = null;
  for (const row of rows) {
    if (row.draft || !row.tag_name) continue;
    if (!isNewer(row.tag_name, current)) continue;
    if (best && !isNewer(row.tag_name, best.version)) continue;
    best = { version: row.tag_name.replace(/^v/, ""), url: row.html_url ?? RELEASES_PAGE };
  }
  return best ? { available: true, ...best } : { available: false };
}

