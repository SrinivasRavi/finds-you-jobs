// Covers: tracker — a Saved card renders on the kanban, the detail modal
// opens, a notes edit lands in the Activity tab, and a column move + the
// exclusive intent value round-trip through the live sidecar (roadmap
// commit-8 UI gate).
//
// The application is created via the API with generation switched OFF —
// letting the UI Save button auto-enqueue tailor/cover here would run the
// real claude-cli engine and spend real tokens inside a test.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/tracker";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("tracker shows a saved card and records activity", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "https://example.com/e2e-tracker-job",
        title: "Staff Engineer",
        company: "Initech",
        location: "Remote",
        description: "Own the monolith.",
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
  expect(application.intent).toBe("none");

  await page.goto("/applications");
  await expect(page.getByText("Staff Engineer").first()).toBeVisible({
    timeout: 15_000,
  });
  await page.screenshot({ path: `${DIR}/tracker-saved-card.png`, fullPage: true });

  // Detail modal: edit notes → the Activity tab records it.
  await page.getByText("Staff Engineer").first().click();
  await expect(page.getByText("Initech").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/tracker-detail.png`, fullPage: true });

  // Column move + exclusive intent via the API — the card re-renders in the
  // new column and the activity log shows the move.
  await request.patch(`${base}/api/applications/${application.id}`, {
    headers: auth,
    data: { column: "seeking_referral", intent: "referral" },
  });
  await request.patch(`${base}/api/applications/${application.id}`, {
    headers: auth,
    data: { intent: "apply" },
  });
  const activity = await (
    await request.get(`${base}/api/applications/${application.id}/activity`, {
      headers: auth,
    })
  ).json();
  const labels = activity.map((e: { label: string }) => e.label);
  expect(labels).toContain("Added to tracker");
  expect(labels).toContain("Moved from Saved to Seeking Referral");
  const final = await (
    await request.get(`${base}/api/applications/${application.id}`, {
      headers: auth,
    })
  ).json();
  expect(final.intent).toBe("apply"); // exclusive — referral fully replaced

  await page.reload();
  await expect(page.getByText("Staff Engineer").first()).toBeVisible({
    timeout: 15_000,
  });
  await page.screenshot({ path: `${DIR}/tracker-moved.png`, fullPage: true });
});

test("apply slot is inert once a card is in the Applied column", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "https://example.com/e2e-applied-job",
        title: "Applied Already Engineer",
        company: "Initech",
        location: "Remote",
        description: "Own the monolith.",
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
  // Move it straight to Applied (never ran the applier — apply_run_status stays none).
  await request.patch(`${base}/api/applications/${application.id}`, {
    headers: auth,
    data: { column: "applied" },
  });

  await page.goto("/applications");
  const card = page.getByText("Applied Already Engineer").first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  // The Apply slot renders a static "Applied" tag on a NON-button span — you
  // can't start a fresh apply run for a job you've already applied to.
  // Scope to THIS card: other specs leave earlier cards with live Apply slots.
  const slot = page
    .getByTestId("tracker-card")
    .filter({ hasText: "Applied Already Engineer" })
    .getByTestId("card-apply-slot");
  await expect(slot).toContainText("Applied");
  await expect(slot).toHaveJSProperty("tagName", "SPAN");
  await page.screenshot({ path: `${DIR}/apply-slot-inert.png`, fullPage: true });
});
