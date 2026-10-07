// Covers: the duplicate prompt and "they're different" (url-less-jobs.md 9.6,
// D15). Driven against a real sidecar, with 2 genuinely near-duplicate rows, so
// the ingest-time flag, the compound gate and the dismissal are all exercised
// for real rather than stubbed.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/duplicate-prompt";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

const JD =
  "Senior Software Engineer at Globex. You will own distributed Python services, " +
  "design PostgreSQL schemas, run workloads on AWS and mentor other engineers. " +
  "Requirements: 6+ years of backend experience, strong Python, solid SQL, and " +
  "production ownership. Nice to have: Go, Kubernetes, and event-driven design.";

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("the scanned card asks once, and never again once dismissed", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });

  // Monday: the user pastes an emailed JD, with no link at all.
  const pasted = await request.post(`${base}/api/jobs`, {
    headers: auth,
    data: {
      canonical_url: "",
      title: "Senior Software Engineer",
      company: "Globex",
      location: "Pune, India",
      description: JD,
      source_adapter: "paste-text",
    },
  });
  const pastedId = (await pasted.json()).id as string;

  // Wednesday: a scan writes its own row for the same posting.
  const scanned = await request.post(`${base}/api/jobs`, {
    headers: auth,
    data: {
      canonical_url: "https://boards.greenhouse.io/globex/jobs/4242",
      title: "Senior Software Engineer",
      company: "Globex",
      location: "Pune, India",
      description: JD,
      source_adapter: "greenhouse",
    },
  });
  const scannedId = (await scanned.json()).id as string;

  await page.goto("/jobs");
  const scannedRow = page
    .getByTestId("job-row")
    .filter({ hasText: "Globex" })
    .first();
  await expect(scannedRow).toBeVisible({ timeout: 15_000 });
  await scannedRow.click();

  const prompt = page.getByTestId("duplicate-prompt");
  await expect(prompt).toBeVisible({ timeout: 10_000 });
  await expect(prompt).toContainText("Senior Software Engineer");
  await page.screenshot({ path: `${DIR}/prompt-on-card.png`, fullPage: true });

  // Compare opens the merge dialog, spelling the outcome out before anything.
  await page.getByTestId("duplicate-compare").click();
  await expect(page.getByTestId("duplicate-merge-confirm")).toBeVisible();
  await page.screenshot({ path: `${DIR}/merge-dialog.png`, fullPage: true });
  await page.getByTestId("duplicate-keep-both").click();
  await expect(page.getByTestId("duplicate-merge-confirm")).toHaveCount(0);

  // "They're different" is remembered, so the prompt goes and stays gone.
  await page.getByTestId("duplicate-different").click();
  await expect(prompt).toHaveCount(0, { timeout: 10_000 });
  await page.reload();
  await page.getByTestId("job-row").filter({ hasText: "Globex" }).first().click();
  await expect(page.getByTestId("duplicate-prompt")).toHaveCount(0, { timeout: 10_000 });
  await page.screenshot({ path: `${DIR}/dismissed.png`, fullPage: true });

  // And it is a fact about the PAIR, so the other card does not ask either.
  const fromOther = await request.get(`${base}/api/jobs/${pastedId}/duplicate`, {
    headers: auth,
  });
  expect(await fromOther.json()).toBeNull();
  expect(scannedId).toBeTruthy();
});
