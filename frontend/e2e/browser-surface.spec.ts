// Covers: the /browser surface against the live sidecar — the screencast
// WebSocket reaches "live", and each navigation paints real frames from the
// sidecar's headless Chrome into the canvas.
//
// This one DOES reach the public internet (example.com, google.com,
// wikipedia.org): the whole point is proving the broker drives a real page.
// Zero model calls.

import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
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

// A coarse, quantized signature of what the screencast canvas is actually
// painting. The canvas is drawn from same-origin JPEG blobs, so getImageData
// is readable (not tainted). Two different pages produce different signatures;
// quantizing the samples keeps a blinking cursor from perturbing it.
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


/** Drive the shared surface the way an operation would — server-side via the
 *  dev navigate route. The surface is WATCH-ONLY in the UI (maintainer,
 *  2026-08-16) and holds ONE viewer, so a second screencast socket would
 *  steal the stream from the page under test. */
async function navigateSurface(
  page: import("@playwright/test").Page,
  request: import("@playwright/test").APIRequestContext,
  base: string,
  token: string,
  url: string,
): Promise<void> {
  // Navigation fails closed without display geometry — state the page's own,
  // exactly what its screencast viewer sends.
  const geometry = await page.evaluate(() => ({
    width: screen.width,
    height: screen.height,
    dpr: window.devicePixelRatio,
  }));
  const res = await request.post(`${base}/api/dev/browser/navigate`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { url, ...geometry },
  });
  if (!res.ok()) {
    throw new Error(`dev navigate failed: ${res.status()} ${await res.text()}`);
  }
}

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
    // The signature the canvas paints before this navigation. After we navigate
    // away, the old page stops producing frames, so the canvas can only settle
    // on the new page — which is why waiting for the signature to leave this
    // value proves the canvas actually caught up, not a stale old-page frame.
    const beforeSig = await page.evaluate(CANVAS_SIG);
    const before = Number(await frameCount.textContent());
    await navigateSurface(page, request, base, token, url);
    // Load-bearing: screencast-page-url is driven ONLY by the sidecar's
    // committed page.url, so a stale paint shows the wrong host and fails here.
    await expect(pageUrl).toContainText(host, { timeout: 20_000 });
    // Frames flowed since the navigate (the transport is live).
    await expect(async () => {
      expect(Number(await frameCount.textContent())).toBeGreaterThan(before);
    }).toPass({ timeout: 20_000 });
    // And the canvas has actually left the previous page. This waits out the
    // screencast latency, so the screenshot below is a trustworthy artifact
    // rather than a race against the pipeline.
    await expect
      .poll(async () => page.evaluate(CANVAS_SIG), { timeout: 20_000, intervals: [200, 300, 500] })
      .not.toBe(beforeSig);
    await page.waitForTimeout(300);
    expect(await page.evaluate(CANVAS_SIG)).not.toBe(beforeSig);
    await expect(page.getByTestId("screencast-error")).toHaveCount(0);
    await page.screenshot({ path: `${DIR}/${name}.png`, fullPage: true });
  }
});

// A LOCAL fixture the broker's Chrome navigates on its own: one page that does
// an in-page SPA route change (history.pushState) and then a full navigation
// (location.assign), with the user typing nothing after the initial Go. The
// URL bar and the bottom line must track both — the surface now pushes its
// committed main-frame URL on every change, not just after a typed navigate.
function spaFixture(): Promise<{ server: Server; base: string }> {
  const server = createServer((req, res) => {
    if (req.url === "/start") {
      res.writeHead(200, { "content-type": "text/html" });
      res.end(
        "<html><body><h1>fixture start</h1><script>" +
          "setTimeout(() => history.pushState({}, '', '/spa-route'), 800);" +
          "setTimeout(() => location.assign('/landed'), 2000);" +
          "</script></body></html>",
      );
      return;
    }
    res.writeHead(200, { "content-type": "text/html" });
    res.end("<html><body><h1>fixture landed</h1></body></html>");
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address() as AddressInfo;
      resolve({ server, base: `http://127.0.0.1:${port}` });
    });
  });
}

test("the read-only URL line tracks SPA and full navigations the user never typed", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate" },
  });
  const fixture = await spaFixture();
  try {
    await page.goto("/browser");
    await expect(page.getByTestId("screencast-status")).toHaveText("live", {
      timeout: 30_000,
    });

    const pageUrl = page.getByTestId("screencast-page-url");
    // Watch-only: there is no bar to type into (maintainer, 2026-08-16).
    await expect(page.getByTestId("browser-url")).toHaveCount(0);

    // Open the fixture the way an operation would.
    await navigateSurface(page, request, base, token, `${fixture.base}/start`);
    await expect(pageUrl).toContainText("/start", { timeout: 20_000 });
    await page.screenshot({ path: `${DIR}/url-track-start.png`, fullPage: true });

    // The page pushState-s itself to /spa-route — nobody typed anything.
    await expect(pageUrl).toContainText("/spa-route", { timeout: 20_000 });
    await page.screenshot({ path: `${DIR}/url-track-spa.png`, fullPage: true });

    // Then it fully navigates itself to /landed — still hands-off.
    await expect(pageUrl).toContainText("/landed", { timeout: 20_000 });
    await expect(page.getByTestId("screencast-error")).toHaveCount(0);
    await page.screenshot({ path: `${DIR}/url-track-landed.png`, fullPage: true });
  } finally {
    fixture.server.close();
  }
});
