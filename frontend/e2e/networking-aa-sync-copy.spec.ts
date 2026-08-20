// Covers: manual-only contact-sync (maintainer decision, 2026-08-15) and the
// honest busy state. The on-open auto-sync is gone, so mounting the Networking
// tab must fire ZERO contact-sync requests; the Sync button fires exactly one;
// the button disables with a spinning icon while the work is genuinely in
// flight; and completion lands visibly — the "Synced just now" stamp appears
// and the kanban's contact list refetches, so a card a sweep moved repaints
// without a manual reload. The tooltip states the manual-only reality and no
// longer mentions the retired on-open path.
//
// ZERO live LinkedIn: the session is dev-marked valid (no cookies exist) and
// the ONE Sync click runs against an EMPTY contact roster — the sync op's
// batch is empty, so the voyager driver is never constructed and no browser
// ever launches (`contact_sync_op.py` builds the driver only for a non-empty
// eligible set). This spec must therefore run on a fresh profile with no
// seeded contacts; it seeds none itself, and it hard-fails before clicking if
// any exist.
//
// The empty sweep settles in well under a second, too fast to reliably catch
// the op-driven half of the busy state here, so the POST is held ~1.2 s via
// route interception to make the visible busy window deterministic. The
// op-tracking half (busy from SSE enqueue to terminal, the ledger seed) is
// unit-covered in `src/api/queries.test.ts` (useContactSyncInFlight).
//
// The `-aa-` in the filename is load-bearing (the aa-onboarding / zz-reconnect
// ordering convention): this spec must run BEFORE networking-browser.spec.ts,
// whose fixture-mode tests seed contacts into the shared profile — with any
// contact present, the empty-roster guard below would (rightly) refuse to
// click Sync.

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
