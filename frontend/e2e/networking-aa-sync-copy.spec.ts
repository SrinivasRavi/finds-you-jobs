// Covers: manual-only contact sync. Opening Networking fires 0 sync requests,
// the Sync button fires 1, shows busy while the work is in flight, then stamps
// "Synced just now" and refetches the kanban. Zero LinkedIn: an empty roster
// means no driver is built, so this needs a fresh profile and refuses to click
// otherwise; `-aa-` makes it run before networking-browser.spec.ts seeds any.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/networking";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

test("mount syncs nothing; Sync fires once, stays busy for the run, and lands visibly", async ({
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

  // Safety net for the zero-LinkedIn discipline: the roster must be empty so
  // the Sync click below cannot build a voyager driver.
  const contacts = await (
    await request.get(`${base}/api/contacts`, { headers: auth })
  ).json();
  expect(contacts).toEqual([]);

  // Count every page-initiated sync POST and contacts GET.
  let syncPosts = 0;
  let contactsGets = 0;
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/api/networking/contact-sync"))
      syncPosts += 1;
    if (req.method() === "GET" && /\/api\/contacts(\?|$)/.test(req.url()))
      contactsGets += 1;
  });
  // Hold the POST ~1.2 s so the busy window is deterministic (see header).
  await page.route("**/api/networking/contact-sync", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.continue();
  });

  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible({ timeout: 15_000 });
  const sync = page.getByTestId("sync-contacts-btn");
  await expect(sync).toBeVisible();

  // 1) Mounting the tab fires NO sync — settle well past any effect timing.
  await page.waitForTimeout(1_500);
  expect(syncPosts).toBe(0);
  // No sync has ever run on this fresh profile, so no stamp either.
  await expect(page.getByTestId("last-synced-stamp")).toHaveCount(0);

  // 2) The tooltip states the manual-only reality.
  const title = await sync.getAttribute("title");
  expect(title).toContain("Runs only when you press Sync");
  expect(title).not.toContain("when you open this tab");
  expect(title).not.toContain("15 minutes");

  // 3) One press → exactly one request, busy (disabled; the spinning icon +
  //    the running note INSIDE the merged fixed-size button, 2026-08-16)
  //    while in flight.
  const getsBeforeClick = contactsGets;
  await sync.click();
  await expect(sync).toBeDisabled();
  await expect(sync).toContainText("Sync");
  await expect(sync).toContainText("Checking contacts");
  await page.screenshot({ path: `${DIR}/sync-busy-during-run.png`, fullPage: true });

  // 4) Completion lands visibly: the stamp appears inside the button
  //    ("Synced just now"), the button re-arms, and the kanban's contact list
  //    refetches (the SSE terminal handler), so any card a sweep moved
  //    repaints immediately.
  const stamp = page.getByTestId("last-synced-stamp");
  await expect(stamp).toBeVisible({ timeout: 15_000 });
  await expect(stamp).toHaveText("Synced just now");
  await expect(sync).toBeEnabled();
  await expect(sync).toContainText("Sync");
  expect(syncPosts).toBe(1);
  await expect.poll(() => contactsGets, { timeout: 5_000 }).toBeGreaterThan(getsBeforeClick);

  await page.screenshot({ path: `${DIR}/sync-manual-only-completed.png`, fullPage: true });

  // Leave the shared profile as found (the networking.spec.ts convention):
  // later specs expect NO LinkedIn session — the tracker-referrals popup test
  // asserts the drafts-only banner — so the dev-marked session must not leak.
  await request.post(`${base}/api/linkedin/disconnect`, { headers: auth });
});
