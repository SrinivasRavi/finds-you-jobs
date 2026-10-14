// Named zz- so it runs last: it kills and respawns the shared sidecar and
// leaves it dead. Covers: the SSE connection state goes live, reconnecting when
// the sidecar dies, live again when it returns, with the operations snapshot
// refetched. A real POST /shutdown, then a respawn on the same port and token
// (Chromium's offline mode doesn't sever loopback).

import { spawn, type ChildProcess } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { E2E_DATA_DIR } from "../playwright.config";
import { sidecarHandshake } from "./_helpers";

const DIR = "e2e/_screenshots/reconnect";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(SPEC_DIR, "..", "..");

function respawnSidecar(port: string, token: string): ChildProcess {
  return spawn("uv", ["run", "python", "-m", "sidecar.app"], {
    cwd: REPO_ROOT,
    detached: true,
    stdio: "ignore",
    env: {
      ...process.env,
      FYJ_PORT: port,
      FYJ_API_TOKEN: token,
      // The same override rule the webServer config applies, so the respawned
      // sidecar reopens the SAME profile the primary one ran on (a caller's
      // FYJ_DATA_DIR — an agent's scratchpad, say — wins over the default).
      FYJ_DATA_DIR: process.env.FYJ_DATA_DIR ?? E2E_DATA_DIR,
      // The webServer env sets this for the primary sidecar; the respawned one
      // must match — a real-CLI respawn would reintroduce the zero-model leak.
      FYJ_FAKE_LLM: "1",
    },
  });
}

test("SSE stream state survives a sidecar restart", async ({ page, request }) => {
  const { port, token } = sidecarHandshake();
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
    // The outage is visible on the main chrome, not just /dev (F-M5).
    await expect(page.getByTestId("stream-reconnecting-banner")).toBeVisible();
    await page.screenshot({ path: `${DIR}/reconnecting.png`, fullPage: true });

    // Bring it back on the same port + token; EventSource retry finds it.
    respawned = respawnSidecar(port, token);
    await expect(page.getByTestId("sse-status")).toHaveText("live", {
      timeout: 30_000,
    });
    await expect(page.getByTestId("stream-reconnecting-banner")).toBeHidden();
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
