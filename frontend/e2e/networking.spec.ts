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

// The card and the contact modal show the thread's REAL last message with
// honest attribution (maintainer, 2026-08-15): "Me:" when the user sent last,
// the contact's first name when the contact did. Only a live contact-sync
// writes `profile_payload.last_thread_message` and this stack never touches
// LinkedIn, so the synced DTO fields are injected at the network edge — the
// sidecar-side persistence and DTO preference are pytest-covered wire-cold
// (test_contact_sync_op.py); what this proves is the real render. The same
// run asserts the removed "Reach out by URL" entry stays gone (maintainer,
// 2026-08-15: it duplicated add-a-contact + the modal composer).
test("card and modal attribute the real last message; reach-out-by-url is gone", async ({
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
  for (const [n, status] of [
    ["Reba Replied", "engagement"],
    ["Owen Outbound", "accepted"],
  ] as const) {
    await request.post(`${base}/api/contacts`, {
      headers: auth,
      data: {
        linkedin_url: `https://www.linkedin.com/in/e2e-${n.split(" ")[0].toLowerCase()}`,
        name: n,
        current_company: "Initech",
        current_role: "Engineer",
        connection_status: status,
      },
    });
  }

  await page.route("**/api/contacts", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const res = await route.fetch();
    const rows = (await res.json()) as Record<string, unknown>[];
    for (const c of rows) {
      if (c.name === "Reba Replied") {
        Object.assign(c, {
          last_message: "Happy to refer you, send me the posting link!",
          last_message_at: new Date().toISOString(),
          last_message_direction: "them",
          last_message_from: "Reba Reyes",
        });
      }
      if (c.name === "Owen Outbound") {
        Object.assign(c, {
          last_message: "Hi Owen, would you have 10 minutes this week?",
          last_message_at: new Date().toISOString(),
          last_message_direction: "me",
          last_message_from: null,
        });
      }
    }
    await route.fulfill({ response: res, body: JSON.stringify(rows) });
  });

  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible({ timeout: 15_000 });

  // Incoming reply → the card names the sender by first name, never "Me:".
  const rebaCard = page.getByTestId("contact-card").filter({ hasText: "Reba Replied" });
  await expect(rebaCard.getByTestId("contact-last-message")).toContainText("Reba:");
  await expect(rebaCard.getByTestId("contact-last-message")).toContainText(
    "Happy to refer you",
  );
  // Our own message last → "Me:".
  const owenCard = page.getByTestId("contact-card").filter({ hasText: "Owen Outbound" });
  await expect(owenCard.getByTestId("contact-last-message")).toContainText("Me:");
  await page.screenshot({
    path: `${DIR}/card-last-message-attribution.png`,
    fullPage: true,
  });

  // The modal attributes the same way — the bare "Last message" label is gone.
  await rebaCard.click();
  await expect(page.getByTestId("contact-modal-last-message")).toContainText("Reba:");
  await expect(page.getByTestId("contact-modal-last-message")).toContainText(
    "Happy to refer you, send me the posting link!",
  );
  await page.screenshot({ path: `${DIR}/modal-incoming-attribution.png` });
  await page.keyboard.press("Escape");

  await owenCard.click();
  await expect(page.getByTestId("contact-modal-last-message")).toContainText("Me:");
  await page.screenshot({ path: `${DIR}/modal-me-attribution.png` });
  await page.keyboard.press("Escape");

  // The one-step reach-out entry is removed; the add-by-URL escape hatch and
  // the modal composer it hands off to remain.
  await expect(page.getByTestId("reach-out-by-url-button")).toHaveCount(0);
  await expect(page.getByTestId("add-contact-by-url-button")).toBeVisible();
  await page.screenshot({ path: `${DIR}/no-reach-out-by-url.png`, fullPage: true });
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
    page.getByText("No contacts yet. Add one by URL from Networking", {
      exact: false,
    }),
  ).toBeVisible();
  await page.screenshot({ path: `${DIR}/referrals-popup-start.png`, fullPage: true });
});

// FR-NW-15 / posture section 1: contact_sync is user-initiated only — the 12 h
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

// US-NW-09 as-reworked 2026-07-30 (posture doc section 5.1): the checkbox multi-select
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

  // Clicking Connect opens the confirm for THIS person. The message box is the
  // EDITOR now (2026-08-16): a textarea, pre-filled, editable in place — the
  // row no longer expands its own draft box in connected mode.
  await send.click();
  await expect(page.getByTestId("reach-out-confirm")).toBeVisible();
  await expect(page.getByText("Send this to Gavin Belson?")).toBeVisible();
  const confirmMsg = page.getByTestId("reach-out-confirm-message");
  await expect(confirmMsg).toHaveValue(/\S/);
  await confirmMsg.fill("Hi Gavin — edited right in the confirm box.");
  await expect(confirmMsg).toHaveValue("Hi Gavin — edited right in the confirm box.");
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

// Column ordering (maintainer ask, 2026-08-16): each column shows its most
// recently active card first — the timestamp the card displays — never the
// sync engine's rotation cursor (whose churn used to reshuffle the board on
// every Sync press). The thread timestamps are injected at the network edge
// exactly like the attribution test above; the order the REAL kanban renders
// is what's asserted.
test("a column orders its cards most-recent activity first", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  // Seeded oldest-activity FIRST, into the otherwise-unused Converted column,
  // so creation order and expected render order are deliberately inverted.
  for (const n of ["Abe Oldest", "Ben Middle", "Cara Recent"]) {
    await request.post(`${base}/api/contacts`, {
      headers: auth,
      data: {
        linkedin_url: `https://www.linkedin.com/in/e2e-${n.split(" ")[0].toLowerCase()}`,
        name: n,
        current_company: "Initech",
        current_role: "Engineer",
        connection_status: "converted",
      },
    });
  }
  const at = (daysAgo: number) =>
    new Date(Date.now() - daysAgo * 24 * 60 * 60_000).toISOString();
  const activity: Record<string, string> = {
    "Abe Oldest": at(9),
    "Ben Middle": at(5),
    "Cara Recent": at(1),
  };
  await page.route("**/api/contacts", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const res = await route.fetch();
    const rows = (await res.json()) as Record<string, unknown>[];
    for (const c of rows) {
      const when = activity[c.name as string];
      if (when) {
        Object.assign(c, {
          last_message: `Note from ${String(c.name)}`,
          last_message_at: when,
          last_message_direction: "them",
          last_message_from: c.name,
        });
      }
    }
    await route.fulfill({ response: res, body: JSON.stringify(rows) });
  });

  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible({ timeout: 15_000 });
  const converted = page.locator('[data-status="converted"]');
  await expect(converted.getByTestId("contact-card")).toHaveCount(3);
  const names = await converted
    .getByTestId("contact-card")
    .locator("h4")
    .allTextContents();
  expect(names).toEqual(["Cara Recent", "Ben Middle", "Abe Oldest"]);
  // The Converted column sits past the kanban's horizontal fold — bring it
  // into the frame so the screenshot actually shows the ordering.
  await converted.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${DIR}/column-most-recent-first.png`, fullPage: true });
});

// The honest sync outcome (2026-08-16): a Sync attempt the read budget cut
// short surfaces a warn note beside the stamp instead of hiding behind
// "Synced just now". The outcome is injected at the network edge (a real
// budget-refusal needs live LinkedIn); the sidecar derivation is
// pytest-covered (test_linkedin_session_n4.py) — what this proves is the
// real header render.
test("a budget-stopped sync shows the warn note inside the Sync button", async ({
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
  const marked = await request.post(`${base}/api/dev/linkedin/mark-session-valid`, {
    headers: auth,
  });
  expect(marked.ok()).toBeTruthy();

  await page.route("**/api/linkedin/session", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const res = await route.fetch();
    const body = (await res.json()) as Record<string, unknown>;
    Object.assign(body, {
      contact_sync_last_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
      contact_sync_last_outcome: {
        at: new Date().toISOString(),
        synced: 0,
        failed: 0,
        stopped: "cap_or_backoff",
        unprobed: 4,
      },
    });
    await route.fulfill({ response: res, body: JSON.stringify(body) });
  });

  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible({ timeout: 15_000 });
  const note = page.getByTestId("sync-stopped-note");
  await expect(note).toBeVisible();
  await expect(note).toContainText("read budget is used up");
  await expect(note).toContainText("4 not checked");
  // ONE status at a time, inside the merged fixed-size button (2026-08-16):
  // the warn note replaces the stamp; the last REAL sweep's time rides its
  // tooltip instead.
  await expect(page.getByTestId("last-synced-stamp")).toHaveCount(0);
  await expect(note).toHaveAttribute("title", /Synced 2h ago/);
  await page.screenshot({ path: `${DIR}/sync-stopped-note.png`, fullPage: true });

  // Leave the shared profile as found (later specs expect no session).
  await page.unrouteAll();
  await request.post(`${base}/api/linkedin/disconnect`, { headers: auth });
});

// A voyager read refusal (self-imposed cap / backoff) must render as the honest
// "Search paused by your rate limits" state with the verbatim pacer reason —
// never as "No contacts found at this company yet" (the Kaseya 2026-08-15 bug:
// a spent profile-view budget looked like a 5k-employee company with nobody in
// it). The sidecar-side refusal propagation (silo envelope → op result_ref →
// candidates endpoint `discover_state: refused`) is pytest-covered wire-cold
// (test_networker_ops_n3.py, test_referral_candidates_recovery.py); what this
// proves is the real render, so the refused payload is injected at the network
// edge. The popup is only OPENED — "Find referrals" is never clicked, so no
// discover op enqueues and the voyager driver is never constructed.
test("referrals popup renders a cap refusal honestly, not as an empty roster", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  const reason = "profile_views cap reached (87/day, plan=sales_navigator)";

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
        canonical_url: "https://example.com/e2e-refused-job",
        title: "Reliability Engineer",
        company: "Initrode",
        location: "Remote",
        description: "Keep the pager quiet.",
        source_adapter: "paste-url",
      },
    })
  ).json();
  await request.post(`${base}/api/applications`, {
    headers: auth,
    data: { job_id: job.id, generate_resume: false, generate_cover: false },
  });
  const marked = await request.post(`${base}/api/dev/linkedin/mark-session-valid`, {
    headers: auth,
  });
  expect(marked.ok()).toBeTruthy();

  await page.route("**/api/jobs/*/referrals/candidates", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        job_id: job.id,
        company: "Initrode",
        candidates: [],
        already_reached_count: 0,
        discover_state: "refused",
        company_confirm: [],
        confirm_url_failed: false,
        refusal_reason: reason,
      }),
    });
  });

  await page.goto("/applications");
  await expect(page.getByText("Reliability Engineer").first()).toBeVisible({
    timeout: 15_000,
  });
  await page.getByTestId("card-referrals-slot").first().click();
  await expect(page.getByTestId("find-referrals-popup")).toBeVisible();
  const refused = page.getByTestId("referrals-refused");
  await expect(refused).toBeVisible();
  await expect(refused).toContainText("Search paused by your rate limits");
  await expect(refused).toContainText(reason);
  // The dishonest empty state must NOT show.
  await expect(page.getByText("No contacts found at this company yet.")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/referrals-refused.png`, fullPage: true });

  // Leave the shared profile as found (later specs expect no session).
  await page.unrouteAll();
  await request.post(`${base}/api/linkedin/disconnect`, { headers: auth });
});
