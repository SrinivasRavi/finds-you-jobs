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
  // Parallel-AI-calls control (2026-07-17): user-tunable 2-20 or Unlimited.
  await expect(page.getByTestId("llm-concurrency-select")).toBeVisible();
  // Subscription-CLI family (2026-07-18): the three verify-only rows render,
  // Antigravity carries its Experimental badge, and each row has a Verify.
  await expect(page.getByTestId("cli-providers-panel")).toBeVisible();
  for (const id of ["claude-cli", "codex-cli", "antigravity-cli"]) {
    await expect(page.getByTestId(`cli-provider-${id}`)).toBeVisible();
    await expect(page.getByTestId(`cli-verify-${id}`)).toBeVisible();
  }
  await expect(page.getByTestId("cli-provider-antigravity-cli")).toContainText("Experimental");
  await page.screenshot({ path: `${DIR}/settings-cli-providers.png`, fullPage: true });
});

test("linkedin session is nested inside referral outreach", async ({ page }) => {
  await page.goto("/settings");
  // Off: no session card, just the unlock hint inside the section.
  await expect(page.getByTestId("linkedin-session-section")).toHaveCount(0);
  // Enable via ack + toggle — the session card appears INSIDE the section as
  // the explicit next step (2026-07-17 dogfood: separated, it read as an
  // unrelated non-experimental setting).
  await page.getByTestId("networking-ack").check();
  await page.getByTestId("networking-toggle").click();
  await expect(page.getByTestId("linkedin-session-section")).toBeVisible();
  await expect(page.getByText("Next step — connect your LinkedIn session")).toBeVisible();
  await page.screenshot({ path: `${DIR}/referral-linkedin-nested.png`, fullPage: true });
  // Turn it back off for later tests.
  await page.getByTestId("networking-toggle").click();
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

test("discovery sources: all on by default, opt-out persists across reload", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("discovery-sources")).toBeVisible({ timeout: 15_000 });
  // Family rows render grouped; the big ones exist with entry counts.
  const greenhouse = page.getByTestId("source-toggle-greenhouse");
  await expect(greenhouse).toBeVisible();
  await expect(page.getByTestId("source-toggle-linkedin")).toBeVisible();
  await expect(greenhouse.locator("input")).toBeChecked();
  await page.screenshot({ path: `${DIR}/discovery-sources-default.png`, fullPage: true });
  // Opt out of Greenhouse → unchecked, and it survives a full reload (it lives
  // in portals_config, not component state). click() + polling assertion, not
  // uncheck(): a React controlled checkbox reverts the native flip until the
  // state round-trips, which uncheck() misreads as "did not change state".
  await greenhouse.locator("input").click();
  await expect(greenhouse.locator("input")).not.toBeChecked();
  await page.reload();
  await expect(page.getByTestId("discovery-sources")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("source-toggle-greenhouse").locator("input")).not.toBeChecked();
  await page.screenshot({ path: `${DIR}/discovery-sources-greenhouse-off.png`, fullPage: true });
  // Back on — leave the shared e2e profile clean for other specs.
  await page.getByTestId("source-toggle-greenhouse").locator("input").click();
  await expect(page.getByTestId("source-toggle-greenhouse").locator("input")).toBeChecked();
});

test("section-title checkboxes flip whole discovery sections at once", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("discovery-sources")).toBeVisible({ timeout: 15_000 });
  // Every section renders its own master checkbox — Apify as its OWN section
  // (2026-07-18 #5), separate from the other search sources.
  for (const kind of ["ats", "board", "search", "apify", "fallback"]) {
    await expect(page.getByTestId(`source-section-toggle-${kind}`)).toBeVisible();
  }
  // No Apify key stored → the section explains how to get actor sources.
  await expect(page.getByText("Save your Apify key below")).toBeVisible();

  // Untick "Job boards": every board family flips off in one click…
  const master = page.getByTestId("source-section-toggle-board");
  await expect(master).toBeChecked();
  await master.click();
  for (const id of ["remoteok", "remotive", "arbeitnow", "themuse", "hackernews"]) {
    await expect(page.getByTestId(`source-toggle-${id}`).locator("input")).not.toBeChecked();
  }
  await page.screenshot({ path: `${DIR}/discovery-section-boards-off.png`, fullPage: true });
  // …persists across reload (portals_config, not component state)…
  await page.reload();
  await expect(page.getByTestId("discovery-sources")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("source-section-toggle-board")).not.toBeChecked();
  // …and one re-ticked row makes the master read mixed (indeterminate).
  await page.getByTestId("source-toggle-remoteok").locator("input").click();
  await expect
    .poll(() =>
      page
        .getByTestId("source-section-toggle-board")
        .evaluate((el) => (el as HTMLInputElement).indeterminate),
    )
    .toBe(true);
  // Master back on restores the whole section for later specs.
  await page.getByTestId("source-section-toggle-board").click();
  await expect(page.getByTestId("source-section-toggle-board")).toBeChecked();
  for (const id of ["remoteok", "remotive", "arbeitnow", "themuse", "hackernews"]) {
    await expect(page.getByTestId(`source-toggle-${id}`).locator("input")).toBeChecked();
  }
});

test("analytics ledger has a Scraper filter chip", async ({ page }) => {
  await page.goto("/analytics");
  await expect(page.getByTestId("agent-filters")).toBeVisible({ timeout: 15_000 });
  // Scraper sits beside Scoring/Tailoring (2026-07-18 #5) and filters to scans.
  await expect(page.getByTestId("agent-chip-scraper")).toBeVisible();
  await expect(page.getByTestId("agent-chip-scoring")).toBeVisible();
  await expect(page.getByTestId("agent-chip-tailoring")).toBeVisible();
  await page.getByTestId("agent-chip-scraper").click();
  await expect(page.getByTestId("agent-chip-scraper")).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: `${DIR}/analytics-scraper-chip.png`, fullPage: true });
});

test("BYO-key rows render and analytics has a Discovery tab", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("discovery-sources")).toBeVisible({ timeout: 15_000 });
  // Key inputs for both providers, no key stored → input + Save visible.
  await expect(page.getByTestId("discovery-credential-apify")).toBeVisible();
  await expect(page.getByTestId("discovery-credential-brave")).toBeVisible();
  await expect(page.getByTestId("discovery-credential-input-apify")).toBeVisible();
  await page.screenshot({ path: `${DIR}/discovery-byok-rows.png`, fullPage: true });

  // Analytics: Costs | Discovery tabs; Discovery renders per-source efficacy.
  await page.goto("/analytics");
  await expect(page.getByTestId("analytics-tabs")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("analytics-tab-discovery").click();
  await expect(page.getByTestId("discovery-panel")).toBeVisible();
  await page.screenshot({ path: `${DIR}/analytics-discovery-tab.png`, fullPage: true });
});

test("linkedin one-shot job search button gates on a connected session", async ({ page }) => {
  await page.goto("/settings");
  // Enable Referral Outreach so the LinkedIn session section reveals.
  await page.getByTestId("networking-ack").check();
  await page.getByTestId("networking-toggle").click();
  await expect(page.getByTestId("linkedin-session-section")).toBeVisible({ timeout: 15_000 });
  // Not connected → the one-shot job-search block must NOT be offered (it uses
  // the logged-in session; no session, no button).
  await expect(page.getByTestId("linkedin-jobsearch-block")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/linkedin-jobsearch-gated.png`, fullPage: true });
  // Leave the shared profile clean.
  await page.getByTestId("networking-toggle").click();
});
