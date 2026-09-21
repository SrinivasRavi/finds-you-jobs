// Covers: the Settings › About & Updates pane. Runs in a real browser (no
// Tauri), so the update check is unavailable — the pane must degrade gracefully
// (version still shows, update controls show the "desktop app" note, no crash)
// and the support/community/source links render.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/about";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

// Get past onboarding: /settings only renders once a profile exists.
test.beforeEach(async ({ request }) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
});

test("about pane shows version and the support/community/source links", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("settings-nav")).toBeVisible({ timeout: 15_000 });

  // The new category exists in the rail and opens its pane.
  await page.getByTestId("settings-nav-about").click();

  // Version renders as "v<numeric>-beta" (from the vite-injected build version
  // in the browser-dev path; getVersion() in the packaged app).
  const version = page.getByTestId("about-version");
  await expect(version).toBeVisible();
  await expect(version).toHaveText(/^v\d+\.\d+\.\d+/);

  // Update controls degrade in the browser: the "desktop app" note shows and
  // the manual check button is absent, which also keeps the e2e run from
  // making an outbound call to GitHub.
  await expect(page.getByTestId("about-update-unavailable")).toBeVisible();
  await expect(page.getByTestId("about-check-updates")).toHaveCount(0);

  // Support / community / source links are all present.
  await expect(page.getByTestId("about-sponsor-link")).toBeVisible();
  await expect(page.getByTestId("about-discord-link")).toBeVisible();
  await expect(page.getByTestId("about-repo-link")).toBeVisible();
  await expect(page.getByTestId("about-issues-link")).toBeVisible();
  // The #prompts-and-configs invitation copy is present.
  await expect(page.getByText("#prompts-and-configs")).toBeVisible();

  // No launch-check preference exists any more: the check is manual only, so
  // the app makes no outbound request the user did not just ask for.
  await expect(page.getByTestId("about-auto-check-toggle")).toHaveCount(0);

  await page.screenshot({ path: `${DIR}/about-pane.png`, fullPage: true });
});
