// Covers: the duplicate prompt and "they're different" (url-less-jobs.md 9.6,
// D15). Driven against a real sidecar, with 2 genuinely near-duplicate rows, so
// the ingest-time flag, the compound gate and the dismissal are all exercised
// for real rather than stubbed.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/duplicate-prompt";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

const JD =
  "Senior Software Engineer at Globex. You will own distributed Python services, " +
  "design PostgreSQL schemas, run workloads on AWS and mentor other engineers. " +
  "Requirements: 6+ years of backend experience, strong Python, solid SQL, and " +
  "production ownership. Nice to have: Go, Kubernetes, and event-driven design.";

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

  // A notice only: it says what to do, and there is nothing to merge with.
  await expect(prompt).toContainText("delete the one you don't need");
  await expect(page.getByTestId("duplicate-compare")).toHaveCount(0);

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

  // Clean up after itself. The e2e profile is shared across the whole suite,
  // and a LEFTOVER url-less job broke `url-less-jobs.spec.ts`, whose
  // "no Add-link slot remains" assertion is board-wide rather than scoped to
  // its own card. Both halves are worth fixing; this is the half that is mine.
  for (const id of [scannedId, pastedId]) {
    await request.post(`${base}/api/jobs/${id}/tombstone`, { headers: auth });
  }
});

// The trust chip's words are translated: a German board says "neu
// ausgeschrieben" and why, never the English sentence or a raw flag.
test("the reposted chip speaks the board's language", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  const jd = JD.replaceAll("Globex", "Initech");
  const ids: string[] = [];
  for (const n of [5151, 5152]) {
    const r = await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: `https://boards.greenhouse.io/initech/jobs/${n}`,
        title: "Senior Software Engineer",
        company: "Initech",
        location: "Pune, India",
        description: jd,
        source_adapter: "greenhouse",
      },
    });
    ids.push((await r.json()).id as string);
  }

  await page.goto("/jobs");
  await page.evaluate(() => localStorage.setItem("fyj-language", "de"));
  await page.reload();
  const chip = page
    .getByTestId("job-row")
    .filter({ hasText: "Initech" })
    .getByTestId("trust-chip")
    .first();
  await expect(chip).toHaveText("neu ausgeschrieben", { timeout: 15_000 });
  await expect(chip).toHaveAttribute("title", /fast identische Anzeige von Initech/);
  await page.screenshot({ path: `${DIR}/chip-de.png`, fullPage: true });
  await page.evaluate(() => localStorage.removeItem("fyj-language"));

  for (const id of ids) {
    await request.post(`${base}/api/jobs/${id}/tombstone`, { headers: auth });
  }
});
