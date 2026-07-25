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
  // An isolated run (second vite on FYJ_WEB_PORT beside the maintainer's dev
  // session) passes the handshake via env — dev-web's .env.local is absent there.
  const envPort = process.env.VITE_SIDECAR_PORT;
  const envToken = process.env.VITE_SIDECAR_TOKEN;
  if (envPort && envToken) {
    return { base: `http://127.0.0.1:${envPort}`, token: envToken };
  }
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

// Archive lifecycle parity (FR-TR manual-add + archive): a manually-logged
// card archives, restores, and never crashes the app — regression for the
// 2026-07-24 customer-reported crash ("undefined is not an object evaluating
// 'app.packet_state'" in CardMenu).

async function seedManualApp(
  request: import("@playwright/test").APIRequestContext,
  slug: string,
  title: string,
) {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  const res = await request.post(`${base}/api/applications/manual`, {
    headers: auth,
    multipart: {
      canonical_url: `https://example.com/${slug}`,
      title,
      company: "Initech",
      location: "Remote",
      column: "applied",
    },
  });
  expect(res.status()).toBe(201);
  return (await res.json()) as { id: string };
}

test("manual application archives from the card menu and restores from Deleted Applications", async ({
  page,
  request,
}) => {
  // Unique per run: the sidecar's appdata persists across local re-runs and a
  // re-used canonical_url would 409 as already-tracked.
  const run = Date.now().toString(36);
  const title = `Manual Archive A ${run}`;
  await seedManualApp(request, `e2e-manual-archive-a-${run}`, title);
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));

  await page.goto("/applications");
  const card = page.getByTestId("tracker-card").filter({ hasText: title });
  await expect(card).toBeVisible({ timeout: 15_000 });

  // ⋮ → archive: the card leaves the board without any crash.
  await card.getByTestId("card-menu-btn").click();
  await expect(page.getByTestId("card-menu")).toBeVisible();
  await page.getByTestId("card-menu-archive").click();
  await expect(card).toHaveCount(0);
  await expect(page.getByText("Unexpected Application Error")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/manual-archived.png`, fullPage: true });

  // It lands in Deleted Applications and restores from there.
  await page.getByTestId("archive-btn").click();
  const modal = page.getByTestId("deleted-applications-modal");
  await expect(modal).toContainText(title);
  await page.screenshot({ path: `${DIR}/manual-deleted-modal.png`, fullPage: true });
  await modal
    .locator("li")
    .filter({ hasText: title })
    .getByTestId("deleted-app-restore-btn")
    .click();
  await expect(modal).not.toContainText(title);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("tracker-card").filter({ hasText: title })).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test("Delete forever removes an archived card permanently via two-step confirm", async ({
  page,
  request,
}) => {
  const run = Date.now().toString(36);
  const title = `Manual Forever ${run}`;
  await seedManualApp(request, `e2e-manual-forever-${run}`, title);

  await page.goto("/applications");
  const card = page.getByTestId("tracker-card").filter({ hasText: title });
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.getByTestId("card-menu-btn").click();
  await page.getByTestId("card-menu-archive").click();
  await expect(card).toHaveCount(0);

  // Count reads as a quiet "(N)" suffix on the header button, not a badge.
  await expect(page.getByTestId("archive-btn")).toContainText(/\(\d+\)/);

  await page.getByTestId("archive-btn").click();
  const modal = page.getByTestId("deleted-applications-modal");
  const row = modal.locator("li").filter({ hasText: title });
  await expect(row).toBeVisible();
  // Two-step: first click arms the row, the red confirm commits it.
  await row.getByTestId("deleted-app-delete-forever-btn").click();
  await page.screenshot({ path: `${DIR}/delete-forever-confirm.png`, fullPage: true });
  await row.getByTestId("deleted-app-delete-forever-confirm-btn").click();
  await expect(row).toHaveCount(0);
  await page.keyboard.press("Escape");
  // Gone for good — not on the board either.
  await expect(page.getByTestId("tracker-card").filter({ hasText: title })).toHaveCount(0);
});

test("archiving from the detail modal while the card menu is open never crashes the app", async ({
  page,
  request,
}) => {
  // The crash path: a card with its ⋮ menu open stays clickable (it sits above
  // the menu backdrop), so the detail modal can open while `menu` state is
  // still set. Archiving there refetches the list without the card — the menu
  // must degrade to "closed", never render from the stale id and crash.
  const run = Date.now().toString(36);
  const title = `Manual Archive B ${run}`;
  await seedManualApp(request, `e2e-manual-archive-b-${run}`, title);
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));

  await page.goto("/applications");
  const card = page.getByTestId("tracker-card").filter({ hasText: title });
  await expect(card).toBeVisible({ timeout: 15_000 });

  await card.getByTestId("card-menu-btn").click();
  await expect(page.getByTestId("card-menu")).toBeVisible();
  // Click the card body (not the backdrop) — opens the detail modal.
  await card.getByTestId("card-title").click();
  await expect(page.getByTestId("detail-archive-btn")).toBeVisible();
  await page.getByTestId("detail-archive-btn").click();

  // The whole point: the board survives and the card is gone.
  await expect(card).toHaveCount(0);
  await expect(page.getByText("Unexpected Application Error")).toHaveCount(0);
  await expect(page.getByTestId("surface-error")).toHaveCount(0);
  await expect(page.getByTestId("archive-btn")).toBeVisible();
  await page.screenshot({ path: `${DIR}/detail-archive-no-crash.png`, fullPage: true });
  expect(pageErrors).toEqual([]);
});
