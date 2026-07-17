// Covers: the restored Settings + Analytics surfaces against the live sidecar
// (feature-parity commit) — Settings renders its section stack (generation
// toggles, engine-routing/prompt rows, networking master toggle, observability
// controls, the P1 no-submit statement) and a prompts-editor edit round-trips
// through /api/settings/prompts; Analytics renders the cost tiles + ledger
// with a real completed operation row, and /logs redirects there.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/settings-analytics";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test.beforeEach(async ({ request }) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
});

test("settings renders every restored section", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("auto-resume-toggle")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("auto-cover-toggle")).toBeVisible();
  await expect(page.getByTestId("prompt-row-score")).toBeVisible();
  await expect(page.getByTestId("networking-toggle")).toBeVisible();
  await expect(page.getByTestId("content-logging-toggle")).toBeVisible();
  await expect(page.getByTestId("retention-days-row")).toBeVisible();
  // The retired auto-submit toggle is GONE; the P1 boundary statement stands.
  await expect(page.getByTestId("apply-mode-toggle")).toHaveCount(0);
  await expect(page.getByTestId("auto-prep-toggle")).toHaveCount(0);
  await expect(page.getByTestId("applier-p1-boundary")).toBeVisible();
  // Auto-score master switch (2026-07-17): on by default with the batch cap
  // visible; off hides the cap and shows the honest disabled note.
  await expect(page.getByTestId("auto-score-toggle")).toBeVisible();
  await expect(page.getByTestId("score-batch-cap-uncapped")).toBeVisible();
  await page.screenshot({ path: `${DIR}/settings-overview.png`, fullPage: true });
  await page.getByTestId("auto-score-toggle").click();
  await expect(page.getByTestId("scoring-disabled-note")).toBeVisible();
  await expect(page.getByTestId("score-batch-cap-uncapped")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/settings-scoring-off.png`, fullPage: true });
  await page.getByTestId("auto-score-toggle").click();
  await expect(page.getByTestId("score-batch-cap-uncapped")).toBeVisible();
});

test("prompts editor round-trips an override", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  await page.goto("/settings");
  await page.getByTestId("prompt-row-score").click();
  await expect(page.getByTestId("prompt-textarea-score")).toBeVisible();
  await expect(page.getByTestId("route-score")).toBeVisible();
  await page.screenshot({ path: `${DIR}/prompts-editor-open.png`, fullPage: true });

  // Round-trip via the API (the editor uses the same PUT): override then reset.
  const put = await request.put(`${base}/api/settings/prompts/score`, {
    headers: auth,
    data: { markdown: "# custom scoring prompt (e2e)" },
  });
  expect(put.status()).toBe(200);
  const listed = await (
    await request.get(`${base}/api/settings/prompts`, { headers: auth })
  ).json();
  const score = listed.find((p: { kind: string }) => p.kind === "score");
  expect(score.override_md).toContain("custom scoring prompt");
  await request.delete(`${base}/api/settings/prompts/score`, { headers: auth });
});

test("analytics shows cost tiles and a real ledger row; /logs redirects", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  // A real zero-LLM operation so the ledger has at least one terminal row.
  await request.post(`${base}/api/operations/cleanup_trash`, { headers: auth, data: {} });
  await expect(async () => {
    const ops = await (
      await request.get(`${base}/api/operations?limit=50`, { headers: auth })
    ).json();
    expect(
      ops.some(
        (o: { kind: string; state: string }) =>
          o.kind === "cleanup_trash" && o.state === "succeeded",
      ),
    ).toBe(true);
  }).toPass({ timeout: 20_000 });

  await page.goto("/logs");
  await expect(page).toHaveURL(/\/analytics$/);
  await expect(page.getByTestId("cost-tiles")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("agent-filters")).toBeVisible();
  await expect(page.getByText("cleanup_trash").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/analytics-ledger.png`, fullPage: true });
});
