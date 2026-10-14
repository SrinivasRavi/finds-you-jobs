// Update check: is a newer release out, and nothing more. 1 GET to the GitHub
// releases API; a newer tag shows a link to the release page, so the OS verifies
// our signature when the download opens and nothing overwrites the running app
// (docs/internal/auto-update.md). Manual only, from Settings › About, so the app
// makes no request the user didn't ask for; unavailable outside Tauri.

import { rawAppVersion } from "./appVersion";
import { inTauri } from "./host";

const REPO = "SrinivasRavi/finds-you-jobs";
const RELEASES_API = `https://api.github.com/repos/${REPO}/releases?per_page=10`;
/** Fallback target when a release carries no html_url of its own. */
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;

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

