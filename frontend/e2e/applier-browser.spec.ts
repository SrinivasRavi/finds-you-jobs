// The Applier's shared browser window (applier/window.md). A run in review has its
// own tab, and "Show in browser" brings it forward. A run that found no Chrome and
// no Chromium offers the one-time download, then Retry (A18). The run is real and
// headless (FYJ_APPLY_DEV); the no-browser outcome is served over the real run.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { expect, test, type APIRequestContext } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/applier-browser";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_FORM = pathToFileURL(
  join(SPEC_DIR, "..", "..", "sidecar", "tests", "packages", "jobapplier", "fixtures", "form.html"),
).href;

async function startRun(request: APIRequestContext, title: string, reviewWaitS: number) {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  await request.patch(`${base}/api/profile/application-profile`, {
    headers: auth,
    data: { name: "E2E Candidate", email: "e2e@example.com", location: "Bengaluru, Karnataka, India" },
  });
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "",
        title,
        company: `${title} Co`,
        location: "Remote",
        description: `${title}: keep the lights on.`,
        source_adapter: "paste-url",
      },
    })
  ).json();
  const application = await (
    await request.post(`${base}/api/applications`, {
      headers: auth,
      data: { job_id: job.id, generate_resume: false, generate_cover: false },
    })
  ).json();
  const run = await (
    await request.post(`${base}/api/applications/${application.id}/apply`, {
      headers: auth,
      data: {
        dev: {
          form_url: FIXTURE_FORM,
          engine_script: [
            JSON.stringify({ tool: "fill", element_id: "e2", value: "Ada Lovelace" }),
            JSON.stringify({ tool: "finish", reason: "grounded fields filled; review the rest" }),
          ],
          allow_local: true,
          headed: false,
          review_wait_s: reviewWaitS,
        },
      },
    })
  ).json();
  return { base, auth, run };
}

test("a run in review shows its tab on request", async ({ page, request }) => {
  test.setTimeout(120_000);
  const { base, auth, run } = await startRun(request, "Window Engineer", 60);
  // In review the run reads `running` at phase `ready_for_human`, its tab open.
  await expect(async () => {
    const snap = await (await request.get(`${base}/api/apply-runs/${run.id}`, { headers: auth })).json();
    expect(snap.phase).toBe("ready_for_human");
  }).toPass({ timeout: 60_000 });

  await page.goto("/applications");
  const slot = page
    .getByTestId("tracker-card")
    .filter({ hasText: "Window Engineer" })
    .getByTestId("card-apply-slot");
  await expect(slot).toBeVisible({ timeout: 15_000 });
  await slot.click();
  const show = page.getByTestId("applier-show-tab-btn");
  await expect(show).toBeVisible();
  await show.click();
  // Shown: no "closed" note.
  await page.waitForTimeout(500);
  await expect(page.getByTestId("applier-tab-gone")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/1-show-in-browser.png`, fullPage: true });

  await page.getByTestId("applier-cancel-btn").click();
  await expect(show).toHaveCount(0, { timeout: 30_000 });
  await page.screenshot({ path: `${DIR}/2-tab-closed-with-run.png`, fullPage: true });
});

test("a run with no browser offers Download, then Retry", async ({ page, request }) => {
  test.setTimeout(180_000);
  const { base, auth, run } = await startRun(request, "No Browser Engineer", 0);
  await expect(async () => {
    const snap = await (await request.get(`${base}/api/apply-runs/${run.id}`, { headers: auth })).json();
    expect(snap.status).toBe("ready_for_human");
  }).toPass({ timeout: 60_000 });
  // This machine has a browser, so the coded failure is served over the real run.
  await page.route(`**/api/apply-runs/${run.id}`, async (route) => {
    const real = await (await route.fetch()).json();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...real,
        status: "failed",
        phase: "failed",
        summary: "Applying needs Chrome or Chromium, and neither is on this computer.",
        blockers: [{ kind: "browser_missing", detail: "no browser", field_label: "" }],
      }),
    });
  });

  await page.goto("/applications");
  const slot = page
    .getByTestId("tracker-card")
    .filter({ hasText: "No Browser Engineer" })
    .getByTestId("card-apply-slot");
  await expect(slot).toBeVisible({ timeout: 15_000 });
  await slot.click();
  const strip = page.getByTestId("applier-browser-download");
  await expect(strip).toContainText("needs Chrome or Chromium");
  await expect(page.getByTestId("applier-retry-btn")).toBeVisible();
  await page.screenshot({ path: `${DIR}/3-offers-download.png`, fullPage: true });

  // The download runs for real; with Chromium already here it's a no-op.
  await page.getByTestId("applier-browser-download-btn").click();
  await expect(strip).toHaveText(/Chromium is ready\. Apply again\./, { timeout: 120_000 });
  await page.screenshot({ path: `${DIR}/4-ready-apply-again.png`, fullPage: true });
});
