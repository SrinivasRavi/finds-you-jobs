// Covers: the Analytics page by hand. Its 2 side tabs, the ledger's filter
// chips, a row's timeline, and the pager over more than 1 page of operations.

import { expect, test } from "@playwright/test";
import { auth, seedProfile, sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/journey-analytics";

test("tabs, filter chips, a row's timeline and the pager", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  await seedProfile(request);
  // Each new job scores at once (Keyword: the description is short), so 55
  // jobs give the ledger more than its 50-row page.
  for (let i = 0; i < 55; i++) {
    await request.post(`${base}/api/jobs`, {
      headers: auth(token),
      data: {
        canonical_url: `https://example.com/journey-ledger-${i}`,
        title: `Ledger Role ${i}`,
        company: "Ledgerco",
        location: "Remote",
        description: "Short.",
        source_adapter: "paste-url",
      },
    });
  }

  await page.goto("/analytics");
  await expect(page.getByTestId("cost-tiles")).toBeVisible();
  await page.getByTestId("analytics-tab-discovery").click();
  await expect(page.getByTestId("analytics-tab-discovery")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("discovery-panel")).toBeVisible();
  await page.getByTestId("analytics-tab-costs").click();
  await expect(page.getByTestId("cost-tiles")).toBeVisible();

  // Pager: page 2 exists and Prev comes back.
  const rows = page.getByTestId("log-row");
  await expect(rows).toHaveCount(50, { timeout: 15_000 });
  await expect(page.getByTestId("logs-prev")).toBeDisabled();
  await page.getByTestId("logs-next").click();
  await expect(page.getByTestId("logs-pager")).toContainText("51");
  await page.screenshot({ path: `${DIR}/ledger-page-2.png`, fullPage: true });
  await page.getByTestId("logs-prev").click();
  await expect(page.getByTestId("logs-pager")).toContainText("1 / ");

  // Filter chips: 1 picked shows only its group; none picked shows all.
  const before = (await page.getByTestId("logs-pager").textContent()) ?? "";
  const tailoring = page.getByTestId("agent-chip-tailoring");
  await tailoring.click();
  await expect(tailoring).toHaveAttribute("aria-pressed", "true");
  await expect(rows.filter({ hasText: "Ledger Role" })).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/ledger-filter-tailoring.png`, fullPage: true });
  await tailoring.click();
  const scoring = page.getByTestId("agent-chip-scoring");
  await scoring.click();
  await expect(rows.first()).toContainText("Ledger Role");
  await scoring.click();
  await expect(scoring).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("logs-pager")).toHaveText(before);

  // A row opens its timeline, and a second click closes it.
  const first = rows.filter({ hasText: "Ledger Role" }).first();
  await first.click();
  await expect(page.getByTestId("log-timeline")).toBeVisible();
  await page.screenshot({ path: `${DIR}/ledger-row-open.png`, fullPage: true });
  await first.click();
  await expect(page.getByTestId("log-timeline")).toHaveCount(0);
});
