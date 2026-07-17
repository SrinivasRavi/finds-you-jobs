// Covers: the Referral Outreach UI against the live sidecar (roadmap
// commits 10-11 gate) — the networking kanban renders seeded contacts in
// their lifecycle columns, a card drags between columns and the move
// persists, and the tracker card's Referrals slot opens the find-referrals
// popup in its drafts-only start state.
//
// ZERO live LinkedIn: contacts are seeded via the manual-add API (the
// rank-don't-gate escape hatch), and the popup is only OPENED — the test
// never clicks "Find referrals" or "Send", so no discover/send op ever
// enqueues and the voyager driver is never constructed.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/networking";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("networking kanban renders seeded contacts and drag persists", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  // The find-referrals/reach-out surfaces sit behind the master networking
  // toggle server-side; the CRM itself is always-on. The profile seed clears
  // the app-wide onboarding gate.
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: { voyager_risk_marker_on: true },
  });
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  const seeded: string[] = [];
  for (const [n, status] of [
    ["Ada Lovelace", "sent"],
    ["Grace Hopper", "accepted"],
  ] as const) {
    const c = await (
      await request.post(`${base}/api/contacts`, {
        headers: auth,
        data: {
          linkedin_url: `https://www.linkedin.com/in/e2e-${n.split(" ")[0].toLowerCase()}`,
          name: n,
          current_company: "Initech",
          current_role: "Engineer",
          connection_status: status,
        },
      })
    ).json();
    seeded.push(c.id);
  }

  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible({ timeout: 15_000 });
  for (const col of ["Sent", "Accepted", "Engagement", "Ghosted", "Converted"]) {
    await expect(page.getByText(col, { exact: true })).toBeVisible();
  }
  await expect(page.getByText("Ada Lovelace")).toBeVisible();
  await expect(page.getByText("Grace Hopper")).toBeVisible();
  await page.screenshot({ path: `${DIR}/networking-kanban.png`, fullPage: true });

  // Drag Ada from Sent into Engagement — the move persists via PATCH
  // /api/contacts (US-NW-07: drag-based column moves, no status dropdown).
  const engagementCol = page.locator('[data-status="engagement"]');
  const card = page.locator(`[data-contact-id="${seeded[0]}"]`);
  await card.dragTo(engagementCol);
  await expect(engagementCol.locator(`[data-contact-id="${seeded[0]}"]`)).toBeVisible({
    timeout: 5_000,
  });
  const contacts = await (
    await request.get(`${base}/api/contacts`, { headers: auth })
  ).json();
  const ada = contacts.find((c: { id: string }) => c.id === seeded[0]);
  expect(ada.connection_status).toBe("engagement");
  await page.screenshot({ path: `${DIR}/networking-drag-moved.png`, fullPage: true });

  // Contact detail modal off the card (US-NW-03) — archive + LinkedIn link,
  // no status dropdown.
  await page.locator(`[data-contact-id="${seeded[1]}"]`).click();
  await expect(page.getByTestId("contact-archive-btn")).toBeVisible();
  await expect(page.getByTestId("contact-status-select")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/contact-detail.png` });
});

test("tracker referrals slot opens the find-referrals popup", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: { voyager_risk_marker_on: true },
  });
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "https://example.com/e2e-networking-job",
        title: "Platform Engineer",
        // A company with no seeded contacts, so the popup opens in its empty
        // manual start state regardless of what the kanban test created.
        company: "Globex",
        location: "Remote",
        description: "Keep the lights on.",
        source_adapter: "paste-url",
      },
    })
  ).json();
  await request.post(`${base}/api/applications`, {
    headers: auth,
    data: { job_id: job.id, generate_resume: false, generate_cover: false },
  });

  await page.goto("/applications");
  await expect(page.getByText("Platform Engineer").first()).toBeVisible({
    timeout: 15_000,
  });
  // The Referrals slot starts in the `none` state and opens the popup.
  await page.getByTestId("card-referrals-slot").first().click();
  await expect(page.getByTestId("find-referrals-popup")).toBeVisible();
  // No LinkedIn session exists, so the popup lands in drafts-only manual
  // mode — the send path stays locked and nothing can touch the wire. Globex
  // has no contacts, so the roster shows the manual-mode empty guidance.
  await expect(page.getByTestId("referrals-drafts-only-banner")).toBeVisible();
  await expect(
    page.getByText("No contacts yet — add one by URL from the Networking page", {
      exact: false,
    }),
  ).toBeVisible();
  await page.screenshot({ path: `${DIR}/referrals-popup-start.png`, fullPage: true });
});
