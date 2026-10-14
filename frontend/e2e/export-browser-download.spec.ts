// A16: when Export finds no Chromium, the Share menu offers the one-time
// download, waits for the sidecar's `browser_install` done event, and the next
// Export renders. The first refusal is simulated (this machine has Chromium);
// the download runs for real and is a no-op here, and so does the render.

import { expect, test } from "@playwright/test";
import { seedProfile } from "./_helpers";

const DIR = "e2e/_screenshots/export-browser-download";

test.beforeEach(async ({ request }) => {
  await seedProfile(request, "# Ada Lovelace\n\nBackend engineer.");
});

test("Export without Chromium offers Download, then exports", async ({ page }) => {
  let refused = false;
  await page.route("**/api/export/pdf", async (route) => {
    if (!refused) {
      refused = true;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { code: "browser_missing", message: "Chromium isn't downloaded yet" },
        }),
      });
      return;
    }
    // Render for real, into the temp folder rather than ~/Downloads.
    const body = { ...(route.request().postDataJSON() as object), destination: "temp" };
    await route.continue({ postData: JSON.stringify(body) });
  });

  await page.goto("/jobs");
  await page.locator("[data-action='open-master-resume']").click();
  await expect(page.getByTestId("master-editor")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("share-btn").click();
  await page.getByTestId("share-export-pdf").click();

  await expect(page.getByTestId("share-export-result")).toHaveText(/needs Chromium/);
  const download = page.getByTestId("share-download-browser");
  await expect(download).toBeVisible();
  await page.screenshot({ path: `${DIR}/1-offers-download.png` });

  await download.click();
  await expect(page.getByTestId("share-browser")).toHaveText(/Chromium is ready/, {
    timeout: 120_000,
  });
  await page.screenshot({ path: `${DIR}/2-ready.png` });

  await page.getByTestId("share-export-pdf").click();
  await expect(page.getByTestId("share-export-result")).toHaveText(/Saved to .*\.pdf/, {
    timeout: 30_000,
  });
  await expect(page.getByTestId("share-browser")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/3-exported.png` });
});
