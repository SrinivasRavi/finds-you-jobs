// Covers: job discovery — the discovered-job board renders real rows from the
// live sidecar (roadmap commit-7 UI gate). Seeds a master profile (satisfies
// the onboarding gate — the wizard itself is a later commit) and two jobs via
// the API, then asserts the board lists them with honest scan status.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/board";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("board lists discovered jobs from the live sidecar", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  // Satisfy the profile gate + seed two distinguishable jobs.
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  for (const [n, title, company] of [
    ["1", "Backend Engineer", "Stripe"],
    ["2", "Platform Engineer", "Acme"],
  ]) {
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: `https://example.com/e2e-job-${n}`,
        title,
        company,
        location: "Remote",
        description: "Build reliable systems.",
        source_adapter: "paste-url",
      },
    });
  }

  await page.goto("/jobs");
  await expect(page.getByText("Backend Engineer").first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText("Platform Engineer").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/board-discovered-jobs.png`, fullPage: true });

  // The JD detail pane opens from a row.
  await page.getByText("Backend Engineer").first().click();
  await expect(page.getByText("Build reliable systems.").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/board-detail.png`, fullPage: true });
});

test("both board search boxes filter server-side and clear back (FR-JB-13)", async ({
  page,
}) => {
  // Uses the two jobs the previous test seeded (Stripe / Acme).
  await page.goto("/jobs");
  const rows = page.locator('[data-testid="job-row"]');
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
  const initial = await rows.count();
  expect(initial).toBeGreaterThanOrEqual(2);

  // List search (title/company/location): company hit narrows to one row.
  const list = page.getByTestId("board-list-search");
  await list.fill("stripe");
  await expect(rows).toHaveCount(1, { timeout: 10_000 });
  await expect(page.getByText("Backend Engineer").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/board-list-search-hit.png`, fullPage: true });
  // A miss shows the explained filter-miss state, never a blank.
  await list.fill("zzz-no-such-job");
  await expect(rows).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByText("No jobs match these filters or search.")).toBeVisible();
  await page.screenshot({ path: `${DIR}/board-list-search-miss.png`, fullPage: true });
  // Clearing restores the unfiltered feed.
  await page.getByTestId("board-list-search-clear").click();
  await expect(rows).toHaveCount(initial, { timeout: 10_000 });

  // Deep search (JD bodies too): a JD-only phrase matches both seeded rows —
  // and the same phrase misses via the shallow list search, proving the split.
  const deep = page.getByTestId("board-text-search");
  await deep.fill("reliable systems");
  await expect(rows).toHaveCount(2, { timeout: 10_000 });
  await page.screenshot({ path: `${DIR}/board-deep-search-hit.png`, fullPage: true });
  await page.getByTestId("board-text-search-clear").click();
  await expect(rows).toHaveCount(initial, { timeout: 10_000 });
  await list.fill("reliable systems");
  await expect(rows).toHaveCount(0, { timeout: 10_000 });
  await page.getByTestId("board-list-search-clear").click();
  await expect(rows).toHaveCount(initial, { timeout: 10_000 });
});
