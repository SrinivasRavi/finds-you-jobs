// Named zz- so it runs LAST (specs execute alphabetically on one worker):
// this test kills and respawns the shared dev-web sidecar, and its cleanup
// leaves that sidecar dead — any spec after it would hit a dead port.
//
// Covers: core storage — the SSE transport renders an honest connection state:
// live → reconnecting when the sidecar really dies → live again once it comes
// back, with the operations snapshot refetched on every reconnect.
//
// The disconnect is real: an authenticated POST /shutdown ends the sidecar the
// webServer (scripts/dev-web.mjs) spawned, then the spec respawns it on the
// SAME port + token (FYJ_PORT / FYJ_API_TOKEN) so the browser's native
// EventSource retry finds it again. Chromium's offline emulation is not used —
// it does not sever loopback connections.

import { spawn, type ChildProcess } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { E2E_DATA_DIR } from "../playwright.config";

const DIR = "e2e/_screenshots/reconnect";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(SPEC_DIR, "..", "..");

function sidecarInfo(): { port: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { port, token };
}

function respawnSidecar(port: string, token: string): ChildProcess {
  return spawn("uv", ["run", "python", "-m", "sidecar.app"], {
    cwd: REPO_ROOT,
    detached: true,
    stdio: "ignore",
    env: {
      ...process.env,
      FYJ_PORT: port,
      FYJ_API_TOKEN: token,
      FYJ_DATA_DIR: E2E_DATA_DIR,
    },
  });
}

test("SSE stream state survives a sidecar restart", async ({ page, request }) => {
  const { port, token } = sidecarInfo();
  let respawned: ChildProcess | null = null;

  try {
    // The dev status surface sits behind the profile gate — seed one via API.
    await request.post(`http://127.0.0.1:${port}/api/profile`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { resume_markdown: "# E2E Candidate" },
    });
    await page.goto("/dev");
    await expect(page.getByTestId("sse-status")).toHaveText("live", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("ops-count")).toContainText(
      "operations recorded:",
    );
    await page.screenshot({ path: `${DIR}/live.png`, fullPage: true });

    // Really end the sidecar (clean drain, same as the shell's quit path).
    const resp = await request.post(`http://127.0.0.1:${port}/shutdown`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(200);

    await expect(page.getByTestId("sse-status")).toHaveText("reconnecting", {
      timeout: 15_000,
    });
    await page.screenshot({ path: `${DIR}/reconnecting.png`, fullPage: true });

    // Bring it back on the same port + token; EventSource retry finds it.
    respawned = respawnSidecar(port, token);
    await expect(page.getByTestId("sse-status")).toHaveText("live", {
      timeout: 30_000,
    });
    await expect(page.getByTestId("ops-count")).toContainText(
      "operations recorded:",
    );
    await page.screenshot({ path: `${DIR}/recovered.png`, fullPage: true });
  } finally {
    // Kill the whole respawned process group (uv wrapper + python child).
    if (respawned?.pid) {
      try {
        process.kill(-respawned.pid, "SIGKILL");
      } catch {
        respawned.kill("SIGKILL");
      }
    }
  }
});
