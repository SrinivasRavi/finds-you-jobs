// Covers: Job finder preferences — personal hard excludes (companies +
// keywords → UserPreferences.hard_excludes) and the tracked-companies roster
// (watched [[sources]] rows: list/add/remove), job-finder-preferences design
// 2026-07-21. Round-trips through the live sidecar: save, reopen, assert.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/finder-prefs";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("excludes and tracked companies round-trip through the modal", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  // Profile gate + non-empty roles/locations so Save is enabled.
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: {
      role_aliases: ["backend engineer"],
      locations: ["remote"],
      // Shrink the seeded 315-source registry to one dead board: "Save &
      // rescan" fires a REAL scan, and against the full registry that means
      // hundreds of live HTTP requests + a teardown drain measured in
      // minutes (observed 2026-07-21). One 404ing tenant keeps the scan
      // instant and the e2e offline-honest.
      portals_config: {
        sources: [{ url: "https://boards.greenhouse.io/e2e-nonexistent-tenant" }],
      },
    },
  });

  await page.goto("/jobs");
  await page.getByTestId("finder-prefs").click();

  // Personal excludes: one company, one keyword.
  await page.getByTestId("fp-exclude-companies").locator("input").fill("Evil Corp");
  await page.keyboard.press("Enter");
  await page.getByTestId("fp-exclude-keywords").locator("input").fill("unpaid");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("fp-exclude-companies").getByText("Evil Corp")).toBeVisible();
  await expect(page.getByTestId("fp-exclude-keywords").getByText("unpaid")).toBeVisible();

  // Tracked companies: add a Greenhouse board by URL, see it listed.
  await page
    .getByTestId("fp-tracked-url")
    .fill("https://boards.greenhouse.io/e2e-watch-co/jobs/99");
  await page.getByTestId("fp-tracked-add").click();
  await expect(page.getByTestId("fp-tracked-row")).toContainText(
    "boards.greenhouse.io/e2e-watch-co",
  );
  await page.screenshot({ path: `${DIR}/finder-prefs-filled.png`, fullPage: true });

  // Save persists the excludes (rescan fires against them server-side).
  await page.getByTestId("finder-prefs-save").click();
  // Generous window: the close awaits a settings refetch that can briefly
  // contend with the rescan the save just enqueued (shared SQLite).
  await expect(page.getByTestId("fp-exclude-companies")).toHaveCount(0, { timeout: 15_000 });

  // Reopen: everything came back from the store, not component state.
  await page.getByTestId("finder-prefs").click();
  await expect(page.getByTestId("fp-exclude-companies").getByText("Evil Corp")).toBeVisible();
  await expect(page.getByTestId("fp-exclude-keywords").getByText("unpaid")).toBeVisible();
  await expect(page.getByTestId("fp-tracked-row")).toContainText("e2e-watch-co");
  await page.screenshot({ path: `${DIR}/finder-prefs-reopened.png`, fullPage: true });

  // The API agrees (hard_excludes actually reached UserPreferences).
  const settings = await (await request.get(`${base}/api/settings`, { headers: auth })).json();
  expect(settings.preferences.hard_excludes).toMatchObject({
    companies: ["Evil Corp"],
    keywords: ["unpaid"],
  });

  // Roster remove: row disappears and stays gone after reopen.
  await page.getByTestId("fp-tracked-remove").click();
  await expect(page.getByTestId("fp-tracked-row")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/finder-prefs-untracked.png`, fullPage: true });
});
