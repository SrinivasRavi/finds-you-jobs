// Covers: the jobs-without-a-URL UI preview (docs/internal/url-less-jobs.md
// section 9). Walks all 16 drawings in the real running app and screenshots
// each state, which is the review artifact section 11 asks every layer for.
// The surface itself is mock-only, so the sidecar is needed for the onboarding
// gate and nothing else.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const DIR = "e2e/_screenshots/url-less";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

async function pick(page: Page, n: number): Promise<void> {
  await page.getByTestId(`urlless-index-${n}`).click();
}

/** A modal drawing opens from the stage button, so the index stays clickable. */
async function openDialog(page: Page): Promise<void> {
  await page.getByTestId("urlless-open-dialog").click();
}

/** Escape, not a backdrop click: the backdrop spans the window, so its centre
 *  is the dialog card itself. The shared Modal closes on Escape. */
async function closeDialog(page: Page): Promise<void> {
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

async function shot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: `${DIR}/${name}.png`, fullPage: true });
}

test("all 16 url-less drawings render and respond", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });

  await page.goto("/url-less");
  await expect(page.getByRole("heading", { name: /UI preview/ })).toBeVisible({ timeout: 15_000 });

  // Drawing 1 — the 5 states of Add a job. State 1: every field visible, the
  // link carries no `required`, and Add job is gated on title + description.
  await pick(page, 1);
  await openDialog(page);
  const link = page.getByTestId("urlless-link-input");
  await expect(link).toBeVisible();
  await expect(page.getByTestId("urlless-add-submit")).toBeDisabled();
  await shot(page, "01-add-job-opened");

  // States 2 and 3: autofill on blur, empty fields only, with Undo.
  await link.fill("https://boards.greenhouse.io/acme/jobs/9");
  await page.getByTestId("urlless-description").click();
  await expect(page.getByTestId("autofill-reading")).toBeVisible();
  await shot(page, "02-add-job-autofilling");
  await expect(page.getByTestId("autofill-filled")).toBeVisible();
  await expect(page.getByTestId("urlless-title")).toHaveValue("Senior Software Engineer");
  await expect(page.getByTestId("urlless-salary")).not.toHaveValue("");
  await expect(page.getByTestId("urlless-add-submit")).toBeEnabled();
  await shot(page, "03-add-job-autofilled");

  // State 4: the sidecar's own refusal, verbatim, with the fields untouched.
  await link.fill("https://careers.acme.io/openings/42");
  await page.getByTestId("urlless-fetch-btn").click();
  await expect(page.getByTestId("autofill-failed")).toBeVisible();
  await expect(page.getByTestId("urlless-title")).toHaveValue("Senior Software Engineer");
  await shot(page, "04-add-job-autofill-failed");

  // State 5: the tombstone stop blocks the save outright.
  await link.fill("https://boards.greenhouse.io/acme/jobs/deleted");
  await page.getByTestId("urlless-fetch-btn").click();
  await expect(page.getByTestId("autofill-tombstoned")).toBeVisible();
  await expect(page.getByTestId("urlless-add-submit")).toBeDisabled();
  await shot(page, "05-add-job-tombstoned");

  // Drawing 2 — triage. A long JD scores with AI and saves nothing; a short one
  // names the keyword downgrade instead of hiding it (section 8.7).
  await closeDialog(page);
  await pick(page, 2);
  await openDialog(page);
  await page.getByTestId("urlless-description").fill("Senior backend role. ".repeat(20));
  await page.getByTestId("urlless-score-first").click();
  await expect(page.getByTestId("urlless-triage-strip")).toBeVisible();
  await expect(page.getByTestId("urlless-triage-strip")).toContainText("claude-sonnet-5");
  await shot(page, "06-triage-ai");
  await page.getByTestId("urlless-description").fill("Short JD.");
  await page.getByTestId("urlless-score-first").click();
  await expect(page.getByTestId("urlless-triage-strip")).toContainText("Keywords");
  await shot(page, "07-triage-keyword-downgrade");

  // Drawing 3 — the tracker's Log an application, same link box and gate.
  await closeDialog(page);
  await pick(page, 3);
  await openDialog(page);
  await expect(page.getByTestId("urlless-log-stage")).toBeVisible();
  await expect(page.getByTestId("urlless-log-submit")).toBeDisabled();
  await shot(page, "08-log-application");

  // Drawings 4 and 5 — the card chips, the Link filter group, the trust tooltip.
  await closeDialog(page);
  await pick(page, 4);
  await expect(page.getByTestId("urlless-no-link-chip").first()).toBeVisible();
  await shot(page, "09-board-card-and-filters");
  await page.getByText("No link", { exact: true }).click();
  await expect(page.getByTestId("urlless-board-row")).toHaveCount(1);
  await shot(page, "10-board-no-link-filter");
  await page.getByText("All", { exact: true }).last().click();
  await pick(page, 5);
  await page.getByTestId("urlless-trust-chip").first().hover();
  await expect(page.getByTestId("urlless-trust-tip")).toBeVisible();
  await shot(page, "11-trust-chip-tooltip");

  // Drawing 6 — the detail action row: Add link where Open posting was, and the
  // careers-page variant of the watch toggle (D12).
  await pick(page, 6);
  await page.getByTestId("urlless-board-row").first().click();
  await expect(page.getByTestId("urlless-add-link")).toBeVisible();
  await expect(page.getByTestId("urlless-watch-company")).toBeVisible();
  await shot(page, "12-detail-action-row");

  // Drawings 7 and 8 — the promotion dialog and the mismatch confirm.
  await pick(page, 7);
  await openDialog(page);
  await page.getByTestId("urlless-attach-input").fill("https://boards.greenhouse.io/acme/jobs/9");
  await shot(page, "13-add-link-dialog");
  await page.getByTestId("urlless-attach-btn").click();
  await expect(page.getByTestId("urlless-toast")).toContainText("Link attached");
  await shot(page, "14-add-link-attached");
  await pick(page, 8);
  await openDialog(page);
  await page.getByTestId("urlless-attach-input").fill("https://uipath.com/jobs/principal-swe");
  await page.getByTestId("urlless-attach-btn").click();
  await expect(page.getByTestId("urlless-mismatch-compare")).toBeVisible();
  await expect(page.getByTestId("urlless-attach-anyway")).toBeVisible();
  await shot(page, "15-mismatch-confirm");

  // Drawings 9 and 10 — the duplicate prompt, the merge ledger, the refusal.
  await closeDialog(page);
  await pick(page, 9);
  await expect(page.getByTestId("urlless-duplicate-prompt")).toBeVisible();
  await shot(page, "16-duplicate-prompt");
  await page.getByRole("button", { name: "Compare" }).click();
  await expect(page.getByTestId("urlless-merge-dialog")).toBeVisible();
  await shot(page, "17-merge-dialog");
  await page.getByTestId("urlless-merge-btn").click();
  await expect(page.getByTestId("urlless-toast")).toContainText("Merged");
  await pick(page, 10);
  await page.getByTestId("urlless-merge-refuse-btn").click();
  await expect(page.getByTestId("urlless-merge-refusal")).toBeVisible();
  await shot(page, "18-merge-refusal");

  // Drawings 11 and 12 — the tracker Overview tab and the Apply prompt.
  await closeDialog(page);
  await pick(page, 11);
  // The slot row stops propagation (same as the shipped card), so click the
  // title rather than the card's centre.
  await page.getByTestId("urlless-tracker-card").getByText("Senior Software Engineer").click();
  await expect(page.getByTestId("urlless-tracker-detail")).toBeVisible();
  await expect(page.getByTestId("urlless-tracker-add-link")).toBeVisible();
  await shot(page, "19-tracker-overview");
  await closeDialog(page);
  await pick(page, 12);
  await page.getByTestId("urlless-apply-slot").click();
  await expect(page.getByText("Where should I apply?")).toBeVisible();
  await shot(page, "20-apply-asks-for-url");

  // Drawings 13 to 16 — the ledger row, the lifecycle copy, delete forever for
  // text, and the empty board's 1 button.
  await closeDialog(page);
  await pick(page, 13);
  await expect(page.getByTestId("urlless-ledger-view-resume")).toBeVisible();
  await shot(page, "21-ledger-row");
  await pick(page, 14);
  await expect(page.getByTestId("urlless-lifecycle-row")).toContainText(
    "never removed automatically",
  );
  await shot(page, "22-lifecycle-copy");
  await pick(page, 15);
  await page.getByTestId("urlless-trash-arm").last().click();
  await expect(page.getByTestId("urlless-trash-text-warning")).toBeVisible();
  await shot(page, "23-delete-forever-for-text");
  await pick(page, 16);
  await expect(page.getByTestId("urlless-empty-add")).toBeVisible();
  await shot(page, "24-empty-board");
  await page.getByTestId("urlless-empty-add").click();
  await expect(page.getByTestId("urlless-link-input")).toBeVisible();
  await shot(page, "25-empty-board-add-job");
});
