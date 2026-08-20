// Covers the Networking surfaces against a live sidecar:
//
//   (A) the LinkedIn browser modal (2026-08-16; a left-rail destination
//       before) — opened from the Networking header's status button, the
//       watch-only broker surface inside a maximum-size dialog: a pageless
//       surface auto-opens its frozen origin ("always on, never a blank
//       canvas") and the read-only URL line tracks the live page.
//   (B) the step-plan panel — explicit idle, then an honest plan for a
//       dry-run send (the real op pipeline, wire cold).
//   (C) the contact-modal composer — stage suggestions, the channel +
//       irreversibility line beside the single Send, the modal auto-opening
//       on send, and the plan panel advancing over the live surface.
//
// The one-step "Reach out by URL" entry is REMOVED (maintainer, 2026-08-15:
// it duplicated "Add a contact by URL" + the contact modal's composer);
// networking.spec.ts asserts its absence.
//
// SAFETY: the surface's frozen origin is really linkedin.com, so every test
// that mounts the modal REQUIRES the e2e stack to be started with
// `VITE_LINKEDIN_ORIGIN=http://127.0.0.1:<port>/` pointing at the loopback
// fixture this spec serves — the surface then auto-opens and navigates the
// FIXTURE only. Without that override those tests skip; they never let a test
// stack touch linkedin.com. Zero model calls, zero account use throughout.

import { createServer, type Server } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const DIR = "e2e/_screenshots/networking-browser";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

// The frozen origin the e2e stack was booted with. Only a loopback origin is
// accepted — anything else (including the real default) skips the tab tests.
const ORIGIN = process.env.VITE_LINKEDIN_ORIGIN ?? "";
const originIsLocalFixture = /^http:\/\/127\.0\.0\.1:\d+\/$/.test(ORIGIN);

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

// A local linkedin-SHAPED fixture site on the overridden origin's port: a home
// page (the auto-open landing) and /in/<slug> profile-ish pages, each visually
// distinct so canvas paints are checkable. Loopback only.
function startFixture(): Promise<Server> {
  const port = Number(/:(\d+)\/$/.exec(ORIGIN)![1]);
  const server = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html" });
    if (req.url === "/" || req.url === "") {
      res.end(
        "<html><body style='background:#eef6ff'><h1>fixture home feed</h1>" +
          "<p>the surface auto-opened its origin</p></body></html>",
      );
      return;
    }
    if (req.url?.startsWith("/in/")) {
      res.end(
        `<html><body style='background:#fff7ee'><h1>fixture profile ${req.url}</h1>` +
          "<textarea rows='6' cols='40' placeholder='compose box'></textarea></body></html>",
      );
      return;
    }
    res.end(`<html><body><h1>fixture page ${req.url}</h1></body></html>`);
  });
  return new Promise((resolve) => {
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}

// A coarse, quantized signature of what the screencast canvas is actually
// painting (copied from browser-surface.spec.ts). Same-origin JPEG blobs make
// getImageData readable; quantizing keeps a blinking cursor from perturbing it,
// so waiting for it to CHANGE proves the canvas caught up to the new page.
const CANVAS_SIG = `(() => {
  const c = document.querySelector('[data-testid="screencast-canvas"]');
  if (!c || !c.width || !c.height) return "blank";
  const ctx = c.getContext("2d");
  const cols = 16, rows = 16;
  let sig = "";
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const x = Math.floor((i + 0.5) * c.width / cols);
      const y = Math.floor((j + 0.5) * c.height / rows);
      const d = ctx.getImageData(x, y, 1, 1).data;
      sig += (d[0] >> 5).toString(8) + (d[1] >> 5).toString(8) + (d[2] >> 5).toString(8);
    }
  }
  return sig;
})()`;

/** Seed a master profile (the /networking route sits behind the profile gate)
 *  and turn on Referral Outreach (the gate the send surfaces live behind). */
async function seedAndEnable(request: APIRequestContext, base: string, token: string) {
  const headers = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers,
    data: { resume_markdown: "# E2E Candidate" },
  });
  // `enabled` mirrors this preference (voyager_risk_marker_on) — no session
  // needed to make the tab + reach-out entry appear.
  await request.post(`${base}/api/settings`, {
    headers,
    data: { voyager_risk_marker_on: true },
  });
}

/** Intercept the reach-out POST at the network edge: the send path is proven
 *  invoked with zero account use. Answers the CORS preflight too (the fetch is
 *  cross-origin from vite's origin to the sidecar's). */
async function interceptReachOut(page: Page): Promise<unknown[]> {
  const posted: unknown[] = [];
  await page.route("**/api/referrals/reach-out", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-headers": "authorization, content-type",
          "access-control-allow-methods": "POST, OPTIONS",
        },
      });
      return;
    }
    posted.push(route.request().postDataJSON());
    await route.fulfill({
      status: 202,
      headers: { "access-control-allow-origin": "*" },
      contentType: "application/json",
      body: JSON.stringify({ enqueued: ["e2e-fake-op"], skipped_contact_ids: [] }),
    });
  });
  return posted;
}

let fixture: Server | null = null;

test.beforeAll(async () => {
  if (originIsLocalFixture) fixture = await startFixture();
});

test.afterAll(async () => {
  fixture?.close();
});

test.beforeEach(async ({ request }) => {
  const { base, token } = sidecarInfo();
  await seedAndEnable(request, base, token);
});

test("LinkedIn browser modal: opened from the status button, watch-only, auto-open home", async ({
  page,
  request,
}) => {
  test.skip(
    !originIsLocalFixture,
    "needs VITE_LINKEDIN_ORIGIN pointed at the loopback fixture — the surface's real origin is linkedin.com, which tests never touch",
  );
  // The modal opens from the header's status button (2026-08-16), which only
  // opens the browser on a live session — expired/never-connected clicks land
  // on Settings instead — so the session is dev-marked valid first.
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  const marked = await request.post(`${base}/api/dev/linkedin/mark-session-valid`, {
    headers: auth,
  });
  expect(marked.ok()).toBeTruthy();

  await page.goto("/networking");
  const pill = page.getByTestId("linkedin-state-pill");
  await expect(pill).toBeVisible();
  await expect(pill).toHaveText("LinkedIn connected");
  await pill.click();
  await expect(page.getByTestId("linkedin-view")).toBeVisible();

  await expect(page.getByTestId("screencast-status")).toHaveText("live", {
    timeout: 30_000,
  });

  // Auto-open home: the pageless surface opened the origin on its own — no
  // dead blank canvas, no "no page yet" — and the read-only line tracks it.
  const pageUrl = page.getByTestId("screencast-page-url");
  await expect(pageUrl).toHaveText(ORIGIN, { timeout: 20_000 });

  // Watch-only (maintainer, 2026-08-16): no URL input, no Go — the user
  // cannot steer the surface mid-operation.
  await expect(page.getByTestId("browser-url")).toHaveCount(0);
  await expect(page.getByTestId("browser-go")).toHaveCount(0);

  const frameCount = page.getByTestId("frame-count");
  await expect(async () => {
    expect(Number(await frameCount.textContent())).toBeGreaterThan(0);
  }).toPass({ timeout: 20_000 });
  await expect
    .poll(async () => page.evaluate(CANVAS_SIG), { timeout: 20_000, intervals: [200, 300, 500] })
    .not.toBe("blank");
  await expect(page.getByTestId("screencast-error")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/browser-modal-home.png`, fullPage: true });

  // Escape closes the dialog; the Networking header is back in reach.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("linkedin-view")).toHaveCount(0);
  await expect(page.getByTestId("networking-kanban")).toBeVisible();

  // Leave the shared profile as found (the networking.spec.ts convention).
  await request.post(`${base}/api/linkedin/disconnect`, { headers: auth });
});

test("browser modal step-plan panel: explicit idle, then an honest plan for a dry-run send", async ({
  page,
  request,
}) => {
  test.skip(
    !originIsLocalFixture,
    "mounts the browser modal, whose frozen origin must be the loopback fixture",
  );
  const { base, token } = sidecarInfo();
  const headers = { Authorization: `Bearer ${token}` };

  // A contact to send to. Created by URL (never navigated to) — zero account use.
  const created = await request.post(`${base}/api/contacts`, {
    headers,
    data: {
      linkedin_url: "https://www.linkedin.com/in/plan-panel-fixture",
      name: "Plan Fixture",
      current_company: "Fixture Co",
      connection_status: "accepted",
    },
  });
  const contactId = ((await created.json()) as { id: string }).id;

  // The status button opens the modal only on a live session (see test A).
  const marked = await request.post(`${base}/api/dev/linkedin/mark-session-valid`, {
    headers,
  });
  expect(marked.ok()).toBeTruthy();
  await page.goto("/networking");
  await page.getByTestId("linkedin-state-pill").click();
  await expect(page.getByTestId("linkedin-view")).toBeVisible();

  // The panel states idleness plainly before anything runs.
  await expect(page.getByTestId("browser-op-plan")).toBeVisible();
  await expect(page.getByTestId("browser-op-plan-idle")).toBeVisible();
  await page.screenshot({ path: `${DIR}/op-plan-idle.png`, fullPage: true });

  // A DRY-RUN send: the real op pipeline (queued → running → settled, with the
  // `sending` phase and its routed channel) with the worker returning before
  // any browser or network is touched. The wire stays cold.
  const res = await request.post(`${base}/api/referrals/reach-out`, {
    headers,
    data: {
      dry_run: true,
      contacts: [{ contact_id: contactId, message: "Hi, checking the plan panel." }],
    },
  });
  expect(res.ok()).toBe(true);

  // The op rides the ONE queue and settles honestly: a dry run never sends,
  // so its row ends "Not sent", named for the contact, with no green check.
  // (The live row's steps-under-a-spinner rendering is unit-covered in
  // BrowserOpPlan.test.tsx — a dry-run op settles too fast to assert the
  // transient live row here without flakes.)
  const settledRow = page.getByTestId("queue-settled");
  await expect(settledRow).toBeVisible({ timeout: 15_000 });
  await expect(settledRow).toContainText("Plan Fixture");
  await expect(settledRow).toContainText("Not sent");
  await expect(settledRow).not.toContainText("✓");
  await page.screenshot({ path: `${DIR}/op-plan-dry-run-send.png`, fullPage: true });

  // Leave the shared profile as found (the networking.spec.ts convention).
  await request.post(`${base}/api/linkedin/disconnect`, { headers });
});

// The maintainer's live report (2026-08-16): "these LinkedIn buttons don't
// add to the queue". The contact modal's LinkedIn link must ENQUEUE a real
// `view_page` operation that shows up in the queue panel and actually paints
// the page — not just open the dialog. The contact's URL is a FIXTURE-ORIGIN
// profile path (never linkedin.com: the op really navigates it).
test("contact modal's LinkedIn link queues a view_page op that lands in the panel and paints", async ({
  page,
  request,
}) => {
  test.skip(
    !originIsLocalFixture,
    "the view op really navigates the surface, so its origin must be the loopback fixture",
  );
  const { base, token } = sidecarInfo();
  const headers = { Authorization: `Bearer ${token}` };

  const created = await request.post(`${base}/api/contacts`, {
    headers,
    data: {
      linkedin_url: `${ORIGIN}in/viewed-fixture`,
      name: "Viewed Fixture",
      current_company: "Fixture Co",
      connection_status: "accepted",
    },
  });
  expect(created.ok()).toBeTruthy();
  const marked = await request.post(`${base}/api/dev/linkedin/mark-session-valid`, {
    headers,
  });
  expect(marked.ok()).toBeTruthy();

  await page.goto("/networking");
  await page.getByTestId("networking-kanban").getByText("Viewed Fixture").click();
  await page.getByTestId("contact-open-linkedin").click();

  // The browser modal opened in place, and the click's view op rides the
  // queue: it settles as a check-only row stating its action + contact
  // ("View: …" — maintainer, 2026-08-16: every row names what it did).
  await expect(page.getByTestId("linkedin-view")).toBeVisible();
  await expect(page.getByTestId("queue-settled").last()).toContainText(
    "View: Viewed Fixture",
    { timeout: 15_000 },
  );
  await expect(page.getByTestId("queue-settled").last()).toContainText("✓");

  // And the surface really shows the profile page the op navigated to.
  await expect(page.getByTestId("screencast-page-url")).toContainText(
    "/in/viewed-fixture",
    { timeout: 20_000 },
  );
  await page.screenshot({ path: `${DIR}/view-page-queued.png`, fullPage: true });

  // Leave the shared profile as found (the networking.spec.ts convention).
  await request.post(`${base}/api/linkedin/disconnect`, { headers });
});

// Stage → suggested-message mapping in the contact detail modal (maintainer
// spec): Accepted/Ghosted get reminders, Engagement gets a referral ask
// personalized with the contact's own employer; a dropdown offers ~3 options
// per stage, the first prefills the editable box. The send is ONE deliberate
// click on the composer itself (the modal is the per-action review surface:
// recipient in the title, the editable message, and the channel +
// irreversibility line beside Send) — no second dialog. The composer's POST is
// intercepted at the network edge; a real dry-run send then drives the plan
// panel over the live fixture surface, so the watchable-send wiring is proven
// wire-cold.
test("contact modal composer: stage suggestions, single-click send, and the watchable browser modal", async ({
  page,
  request,
}) => {
  test.skip(
    !originIsLocalFixture,
    "the send auto-opens the browser modal, whose frozen origin must be the loopback fixture",
  );
  const { base, token } = sidecarInfo();
  const headers = { Authorization: `Bearer ${token}` };

  const seed = async (name: string, company: string, status: string) => {
    const res = await request.post(`${base}/api/contacts`, {
      headers,
      data: {
        linkedin_url: `https://www.linkedin.com/in/${name.toLowerCase().replace(/ /g, "-")}`,
        name,
        current_company: company,
        connection_status: status,
      },
    });
    return ((await res.json()) as { id: string }).id;
  };
  await seed("Ada Accepted", "Northline", "accepted");
  const eveId = await seed("Eve Engaged", "Fixture Systems", "engagement");
  const ghostedId = await seed("Gus Ghosted", "Northline", "sent");
  // `ghosted` isn't a creation column — move the card the kanban way.
  await request.patch(`${base}/api/contacts/${ghostedId}`, {
    headers,
    data: { connection_status: "ghosted" },
  });

  const posted = await interceptReachOut(page);

  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible();

  const openCard = async (name: string) => {
    await page.getByTestId("networking-kanban").getByText(name).click();
    await expect(page.getByTestId("contact-compose-message")).toBeVisible();
  };
  const boxValue = () =>
    page.getByTestId("contact-compose-message").inputValue();

  // Accepted → a reminder (they accepted but never responded).
  await openCard("Ada Accepted");
  expect(await boxValue()).toContain("Hi Ada,");
  expect(await boxValue()).toContain("Thanks for connecting");
  await page.screenshot({ path: `${DIR}/compose-accepted.png`, fullPage: true });
  await page.keyboard.press("Escape");

  // Ghosted → a reminder after a long silence.
  await openCard("Gus Ghosted");
  expect(await boxValue()).toContain("Hi Gus,");
  expect(await boxValue()).toContain("top of your inbox");
  await page.screenshot({ path: `${DIR}/compose-ghosted.png`, fullPage: true });
  await page.keyboard.press("Escape");

  // Engagement → the referral ask, personalized with THEIR employer.
  await openCard("Eve Engaged");
  expect(await boxValue()).toContain(
    "Can you please refer me for a role at Fixture Systems?",
  );
  // The dropdown swaps in another phrasing; the box refills.
  await page
    .getByTestId("contact-compose-template")
    .selectOption("engagement-soft");
  expect(await boxValue()).toContain("No pressure at all.");

  // The channel + irreversibility line sits beside the single Send button and
  // states the REAL channel (not 1st-degree → a connection request).
  await expect(page.getByTestId("contact-compose-channel")).toContainText(
    "connection request",
  );
  await expect(page.getByTestId("contact-compose-channel")).toContainText(
    "can't take it back",
  );
  await expect(page.getByTestId("contact-compose-send")).toHaveText("Send");
  await page.screenshot({ path: `${DIR}/compose-single-send.png`, fullPage: true });

  // Edit freely, then ONE click sends through the one gated path — no second
  // dialog — and the browser modal opens in place, live from the first step.
  await page
    .getByTestId("contact-compose-message")
    .fill("Hi Eve, edited by hand before sending.");
  await page.getByTestId("contact-compose-send").click();

  await expect(page.getByTestId("reach-out-confirm")).toHaveCount(0);
  // The send auto-opened the browser modal (app-level since 2026-08-16); the
  // route stays on Networking underneath — no navigation.
  await expect(page).toHaveURL(/\/networking$/);
  await expect(page.getByTestId("linkedin-view")).toBeVisible();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toMatchObject({
    contacts: [{ message: "Hi Eve, edited by hand before sending." }],
  });

  // The surface is live (auto-opened on the fixture origin, or already there
  // from an earlier test on this shared slug) and the panel sits beside it.
  await expect(page.getByTestId("screencast-status")).toHaveText("live", {
    timeout: 30_000,
  });
  await expect(page.getByTestId("browser-op-plan")).toBeVisible();

  // Now a REAL dry-run send (the intercept above binds only the page's own
  // fetches, not this API call): the plan panel advances on the op's honest
  // signals while the live surface streams beside it — the watchable-send
  // wiring, proven wire-cold.
  const res = await request.post(`${base}/api/referrals/reach-out`, {
    headers,
    data: {
      dry_run: true,
      contacts: [{ contact_id: eveId, message: "Hi Eve, checking the live view." }],
    },
  });
  expect(res.ok()).toBe(true);
  // The op lands in the one queue and settles honestly beside the live view.
  await expect(page.getByTestId("queue-settled").last()).toContainText("Not sent", {
    timeout: 15_000,
  });
  await page.screenshot({ path: `${DIR}/watchable-send.png`, fullPage: true });
});
