// Covers: the /browser surface against the live sidecar — the screencast
// WebSocket reaches "live", and each navigation paints real frames from the
// sidecar's headless Chrome into the canvas.
//
// This one DOES reach the public internet (example.com, google.com,
// wikipedia.org): the whole point is proving the broker drives a real page.
// Zero model calls.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/browser";
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

const PAGES = [
  { name: "example", url: "https://example.com", host: "example.com" },
  { name: "google", url: "https://www.google.com", host: "google.com" },
  { name: "wikipedia", url: "https://www.wikipedia.org", host: "wikipedia.org" },
] as const;

test("screencast: a live stream that paints frames for every navigation", async ({
  page,
  request,
}) => {
  // /browser sits behind the profile gate — seed one via the API.
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate" },
  });
  await page.goto("/browser");

  await expect(page.getByTestId("screencast-status")).toHaveText("live", {
    timeout: 30_000,
  });

  const frameCount = page.getByTestId("frame-count");
  const pageUrl = page.getByTestId("screencast-page-url");
  for (const { name, url, host } of PAGES) {
    // Snapshot the frame count at click time, before this navigation paints,
    // so a static page whose compositor goes idle after its first render still
    // proves a repaint. Sampling after the commit misses that render.
    const before = Number(await frameCount.textContent());
    await page.getByTestId("browser-url").fill(url);
    await page.getByTestId("browser-go").click();
    // The load-bearing check: screencast-page-url is driven ONLY by the
    // sidecar's committed page.url, so a stale paint shows the wrong host and
    // fails here even while frames keep climbing.
    await expect(pageUrl).toContainText(host, { timeout: 20_000 });
    // And the new page produced at least one paint of its own since the click,
    // which a stale paint of the old host can't satisfy alongside the URL match.
    await expect(async () => {
      expect(Number(await frameCount.textContent())).toBeGreaterThan(before);
    }).toPass({ timeout: 20_000 });
    await expect(page.getByTestId("screencast-error")).toHaveCount(0);
    await page.screenshot({ path: `${DIR}/${name}.png`, fullPage: true });
  }
});
