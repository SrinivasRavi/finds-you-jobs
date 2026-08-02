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
    // Aliases/locations keep the seeded job inside the Job-finder preferences
    // filter (career-ops filter parity, discovery.md 2026-07-21).
    data: {
      voyager_risk_marker_on: true,
      role_aliases: ["platform engineer"],
      locations: ["Remote"],
    },
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

  // With networking ON, the Job Board's JD pane regains its per-job
  // Find-referrals toggle (US-JB-03 / US-NW-09, restored 2026-07-17).
  // Visit /jobs BEFORE creating the application: the board excludes saved
  // jobs server-side, so once the application below exists this row is gone
  // from the feed (2026-08-02 — this ordering, not the prefs filter, was why
  // the row never rendered).
  await page.goto("/jobs");
  await page.getByText("Platform Engineer").first().click();
  await expect(page.getByTestId("jd-referrals-toggle")).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${DIR}/jd-referrals-toggle.png`, fullPage: true });

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

// FR-NW-15 / posture §1: contact_sync is user-initiated only — the 12 h
// schedule that touched LinkedIn with nobody present is gone. Two things to
// verify visually: the Sync control is ABSENT until the feature is actually
// usable (toggle on AND a live session), and PRESENT once it is.
//
// ZERO live LinkedIn: the session is marked valid through the FYJ_DEV-only
// route, which writes no cookies. The test never CLICKS Sync — same discipline
// this file already uses for the reach-out popup, so no contact_sync op ever
// enqueues and no browser is ever launched at linkedin.com.

test("Sync is gated on a live session and never runs on a timer", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: { voyager_risk_marker_on: true },
  });

  // No scheduled contact_sync exists at all — this is the regression that
  // matters most, because a timer is what broke "no LinkedIn traffic without a
  // user present".
  const schedules = await (await request.get(`${base}/api/schedules`, { headers: auth })).json();
  expect(schedules.map((s: { kind: string }) => s.kind)).not.toContain("contact_sync");

  // Toggle on but no session → the control must not be offered.
  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("sync-contacts-btn")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/sync-absent-no-session.png`, fullPage: true });

  // Now a live session → the control appears.
  const marked = await request.post(`${base}/api/dev/linkedin/mark-session-valid`, {
    headers: auth,
  });
  expect(marked.ok()).toBeTruthy();

  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible({ timeout: 15_000 });
  const sync = page.getByTestId("sync-contacts-btn");
  await expect(sync).toBeVisible();
  await expect(sync).toBeEnabled();
  await page.screenshot({ path: `${DIR}/sync-present-with-session.png`, fullPage: true });
});

// US-NW-09 as-reworked 2026-07-30 (posture doc §5.1): the checkbox multi-select
// + "Reach out (N)" batch became a per-row Connect/Message button, each opening
// a pre-send confirmation for exactly one contact that shows the message text.
//
// ZERO live LinkedIn: the session is dev-marked valid (no cookies exist), and
// the test opens the confirm overlay — pure local UI — then CANCELS. Send is
// never clicked, so no send op ever enqueues and no browser launches.

test("referral rows send one at a time via a per-row confirm", async ({
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
  await request.post(`${base}/api/dev/linkedin/mark-session-valid`, { headers: auth });

  // A role at Hooli plus one Hooli contact → the popup boots straight into
  // review with a roster (no discovery runs when candidates already exist).
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "https://example.com/e2e-rowwise-job",
        title: "Staff Engineer",
        company: "Hooli",
        location: "Remote",
        description: "Ship it.",
        source_adapter: "paste-url",
      },
    })
  ).json();
  await request.post(`${base}/api/applications`, {
    headers: auth,
    data: { job_id: job.id, generate_resume: false, generate_cover: false },
  });
  await request.post(`${base}/api/contacts`, {
    headers: auth,
    data: {
      linkedin_url: "https://www.linkedin.com/in/e2e-gavin",
      name: "Gavin Belson",
      current_company: "Hooli",
      current_role: "Engineer",
      connection_status: "sent",
    },
  });

  await page.goto("/applications");
  await expect(page.getByText("Staff Engineer").first()).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("card-referrals-slot").first().click();
  await expect(page.getByTestId("find-referrals-popup")).toBeVisible();

  const row = page.getByTestId("referrals-row").filter({ hasText: "Gavin Belson" });
  await expect(row).toBeVisible({ timeout: 15_000 });
  // The multi-select is gone — no checkbox anywhere, a per-row send button
  // instead ("Connect" for a cold non-1st-degree contact).
  await expect(page.getByTestId("referrals-row-checkbox")).toHaveCount(0);
  const send = row.getByTestId("referrals-row-send");
  await expect(send).toHaveText("Connect");
  await page.screenshot({ path: `${DIR}/referrals-rowwise-buttons.png`, fullPage: true });

  // Clicking Connect opens the confirm for THIS person, message text shown.
  await send.click();
  await expect(page.getByTestId("reach-out-confirm")).toBeVisible();
  await expect(page.getByText("Send this to Gavin Belson?")).toBeVisible();
  await expect(page.getByTestId("reach-out-confirm-message")).not.toBeEmpty();
  await page.screenshot({ path: `${DIR}/referrals-rowwise-confirm.png`, fullPage: true });

  // Cancel — nothing sends, the roster is still there.
  await page.getByText("Cancel", { exact: true }).click();
  await expect(page.getByTestId("reach-out-confirm")).toHaveCount(0);
  await expect(row).toBeVisible();
});

// Settings surface for the caps work: the membership × risk% rate-limit card
// and the job-search pull capped at 25.
// ZERO live LinkedIn: only local settings mutations; Search is never clicked.

test("settings expose the rate-limit controls and the 25-job search cap", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  await request.post(`${base}/api/dev/linkedin/mark-session-valid`, { headers: auth });

  // With BOTH LinkedIn opt-ins off, the rate-limits card is hidden — caps for
  // features you haven't enabled are pure noise (maintainer 2026-08-02).
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: { voyager_risk_marker_on: false },
  });
  await page.goto("/settings");
  await page.getByTestId("settings-nav-networking").click();
  await expect(page.getByTestId("linkedin-membership-select")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/settings-rate-limits-hidden.png`, fullPage: true });

  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: { voyager_risk_marker_on: true },
  });
  await page.reload();
  // Self-imposed rate limits: lives in the Networking category beside the
  // shared LinkedIn session it scopes to (maintainer 2026-08-02 — feature
  // configs sit in their feature's category; only lifecycle + logs stay under
  // Data). One membership dropdown + risk slider drive all caps; membership
  // replaces the old New/Seasoned tier + Free/Premium plan selectors.
  await page.getByTestId("settings-nav-networking").click();
  const membership = page.getByTestId("linkedin-membership-select");
  await membership.scrollIntoViewIfNeeded();
  await expect(membership).toBeVisible();
  await expect(membership).toHaveValue("free"); // conservative default
  const risk = page.getByTestId("linkedin-risk-slider");
  await expect(risk).toHaveValue("60"); // default reproduces today's caps
  // Each cap is a dropdown bounded at the estimated ceiling (you can't pick
  // above the max); it's pre-selected to the effective cap (invites/week = 30).
  await expect(page.getByTestId("linkedin-cap-select-invites_week")).toHaveValue("30");
  await membership.selectOption("premium");
  await expect(membership).toHaveValue("premium");
  await page.screenshot({ path: `${DIR}/settings-rate-limits.png`, fullPage: true });
  await membership.selectOption("free"); // leave state as found for later tests

  // The logged-in job search lives in the Discover-jobs settings category and
  // pulls exactly one LinkedIn page (25) — fixed, not a selector: the request
  // carries count=25 whatever we ask for, so a knob would only be cosmetic.
  await page.getByRole("button", { name: /Sources, scoring & automation/ }).click();
  const ack = page.getByTestId("linkedin-search-ack");
  await ack.scrollIntoViewIfNeeded();
  await ack.check();
  await page.getByTestId("linkedin-search-toggle").click();
  const limit = page.getByTestId("linkedin-jobsearch-limit");
  await expect(limit).toBeVisible();
  await expect(limit).toHaveText("25 jobs (one page)");
  await expect(limit.locator("option")).toHaveCount(0); // no selector at all
  await page.screenshot({ path: `${DIR}/settings-search-cap-25.png`, fullPage: true });

  // Fresh search / Next page (2026-08-01): Next page renders only while a
  // continuable cursor exists. Seeded via the dev route — a real Fresh search
  // would need a genuinely usable LinkedIn session, which a test never has.
  const freshBtn = page.getByTestId("linkedin-jobsearch-btn");
  await expect(freshBtn).toBeVisible();
  await expect(freshBtn).toHaveText("Fresh search");
  await expect(page.getByTestId("linkedin-jobsearch-next-btn")).toHaveCount(0);
  await request.post(`${base}/api/dev/linkedin/seed-search-cursor`, { headers: auth });
  await page.reload();
  await page.getByRole("button", { name: /Sources, scoring & automation/ }).click();
  const nextBtn = page.getByTestId("linkedin-jobsearch-next-btn");
  await nextBtn.scrollIntoViewIfNeeded();
  await expect(nextBtn).toBeVisible();
  await expect(nextBtn).toHaveText("Next page");
  await page.screenshot({ path: `${DIR}/settings-search-next-page.png`, fullPage: true });

  // Leave the shared profile as found: search off again, session disconnected
  // (settings-analytics asserts the block is ABSENT while not connected; the
  // disconnect also clears the seeded pagination cursor).
  await page.getByTestId("linkedin-search-toggle").click();
  await request.post(`${base}/api/linkedin/disconnect`, { headers: auth });
});
