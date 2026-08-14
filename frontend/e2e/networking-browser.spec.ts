// Covers Phase 8's two Networking surfaces against a live sidecar:
//
//   (A) the Browser tab — the core broker surface promoted into the add-on's
//       Networking tab. It attaches to the LinkedIn broker slug, but this spec
//       drives it to a FIXTURE page (example.com), never linkedin.com: the point
//       is proving the tab streams and steers a real surface, with ZERO account
//       use. Fresh test profile ⟹ the "linkedin" surface is a blank Chrome.
//   (B) the paste-a-URL reach-out — pasting a profile URL files the contact on
//       the kanban, then opens the per-action confirm composer. The confirm is
//       asserted to OPEN; it is never sent (no account use).
//
// Both surfaces live behind the Referral Outreach opt-in, so the setup enables
// it via the settings API before navigating. Zero model calls.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type APIRequestContext } from "@playwright/test";

const DIR = "e2e/_screenshots/networking-browser";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

// A stable, minimal, non-LinkedIn page to steer the surface to.
const FIXTURE = { url: "https://example.com", host: "example.com" };

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
 *  and turn on Referral Outreach (the gate both new surfaces live behind). */
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

test.beforeEach(async ({ request }) => {
  const { base, token } = sidecarInfo();
  await seedAndEnable(request, base, token);
});

test("Networking Browser tab: streams the broker surface and drives its URL bar", async ({
  page,
}) => {
  await page.goto("/networking");

  // The tab bar only exists once Referral Outreach is on — proves the gate.
  await expect(page.getByTestId("networking-tabs")).toBeVisible();
  await page.getByTestId("networking-tab-browser").click();

  // The broker surface (the LinkedIn slug) comes up — a fresh, blank Chrome.
  await expect(page.getByTestId("screencast-status")).toHaveText("live", {
    timeout: 30_000,
  });

  const frameCount = page.getByTestId("frame-count");
  const pageUrl = page.getByTestId("screencast-page-url");
  const beforeSig = await page.evaluate(CANVAS_SIG);
  const before = Number(await frameCount.textContent());

  await page.getByTestId("browser-url").fill(FIXTURE.url);
  await page.getByTestId("browser-go").click();

  // page-url is driven ONLY by the sidecar's committed page.url, so a stale
  // paint would show the wrong host and fail here.
  await expect(pageUrl).toContainText(FIXTURE.host, { timeout: 20_000 });
  await expect(async () => {
    expect(Number(await frameCount.textContent())).toBeGreaterThan(before);
  }).toPass({ timeout: 20_000 });
  // The canvas actually left the previous page — waits out screencast latency so
  // the screenshot is a trustworthy artifact, not a race against the pipeline.
  await expect
    .poll(async () => page.evaluate(CANVAS_SIG), { timeout: 20_000, intervals: [200, 300, 500] })
    .not.toBe(beforeSig);
  await page.waitForTimeout(300);
  expect(await page.evaluate(CANVAS_SIG)).not.toBe(beforeSig);
  await expect(page.getByTestId("screencast-error")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/browser-tab.png`, fullPage: true });
});

test("paste-a-URL reach-out: files the contact on the kanban and opens the confirm composer", async ({
  page,
}) => {
  await page.goto("/networking");
  await expect(page.getByTestId("networking-kanban")).toBeVisible();

  await page.getByTestId("reach-out-by-url-button").click();
  await expect(page.getByTestId("reach-out-by-url-form")).toBeVisible();

  // A fixture profile URL — never navigated to; add-contact only stores the
  // string on a kanban row, so this is zero account use.
  await page
    .getByTestId("reach-out-by-url-input")
    .fill("https://www.linkedin.com/in/fixture-person-e2e");
  await page.getByTestId("reach-out-by-url-name").fill("Fixture Person");
  await page
    .getByTestId("reach-out-by-url-message")
    .fill("Hi Fixture, I'd love to connect about roles on your team.");
  await page.getByTestId("reach-out-by-url-submit").click();

  // The per-action confirm composer opens over the compose form…
  await expect(page.getByTestId("reach-out-confirm")).toBeVisible();
  await expect(page.getByTestId("reach-out-confirm-message")).toContainText(
    "roles on your team",
  );

  // …and the contact has landed on the kanban behind it (in the DOM even while
  // the confirm overlay covers it).
  await expect(
    page.getByTestId("networking-kanban").getByText("Fixture Person"),
  ).toBeVisible();

  await page.screenshot({ path: `${DIR}/reach-out-confirm.png`, fullPage: true });

  // Deliberately NOT sending — the confirm is the account-touching step, and this
  // spec touches no account.
});
