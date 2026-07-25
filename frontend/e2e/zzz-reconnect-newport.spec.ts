// Named zzz- so it runs LAST, after zz-reconnect.spec.ts (specs execute
// alphabetically on one worker): both specs kill the shared dev-web sidecar,
// and this file's cleanup (each test kills its own respawned sidecar) leaves
// it dead for good.
//
// Covers: F-H3 — the SSE stream survives a shell-style kill-restart where the
// sidecar comes back on a DIFFERENT port with a DIFFERENT token. The EventBus
// bakes the handshake into the EventSource URL, so native retry alone would
// hammer the dead old port forever; after a few failures it must re-resolve
// the handshake and rebuild. REST recovers through the same re-resolution
// (RealApi.req), which is what lets the profile gate come back.
//
// Honest gap: outside Tauri there is no shell to re-ask, so the re-resolved
// handshake comes from the `window.__FYJ_SIDECAR_INFO__` e2e seam
// (src/api/client.ts) instead of the shell's `get_sidecar_port`/`get_api_token`
// commands. The rebuild path under test — dead baked-in URL → N failures →
// close → fresh getSidecarInfo() → new EventSource → live — is the production
// code path; only the handshake SOURCE is injected.

import { spawn, type ChildProcess } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { E2E_DATA_DIR } from "../playwright.config";

const DIR = "e2e/_screenshots/reconnect-newport";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(SPEC_DIR, "..", "..");

function sidecarInfo(): { port: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { port, token };
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      if (addr == null || typeof addr === "string") {
        reject(new Error("no port assigned"));
        return;
      }
      srv.close(() => resolve(addr.port));
    });
    srv.on("error", reject);
  });
}

function spawnSidecar(port: number, token: string): ChildProcess {
  return spawn("uv", ["run", "python", "-m", "sidecar.app"], {
    cwd: REPO_ROOT,
    detached: true,
    stdio: "ignore",
    env: {
      ...process.env,
      FYJ_PORT: String(port),
      FYJ_API_TOKEN: token,
      FYJ_DATA_DIR: E2E_DATA_DIR,
      FYJ_FAKE_LLM: "1",
    },
  });
}

test("SSE stream recovers when the sidecar restarts on a NEW port + token", async ({
  page,
  request,
}) => {
  const old = sidecarInfo();
  let respawned: ChildProcess | null = null;

  try {
    // End the original sidecar (zz-reconnect may already have left it dead).
    await request
      .post(`http://127.0.0.1:${old.port}/shutdown`, {
        headers: { Authorization: `Bearer ${old.token}` },
      })
      .catch(() => undefined);

    // Respawn on a genuinely different port with a different token — the
    // shell-supervisor restart shape (each spawn = new random port + token).
    const newPort = await freePort();
    const newToken = `e2e-restart-${Date.now()}`;
    respawned = spawnSidecar(newPort, newToken);
    await expect
      .poll(
        () =>
          request
            .get(`http://127.0.0.1:${newPort}/healthz`, {
              headers: { Authorization: `Bearer ${newToken}` },
            })
            .then((r) => r.status())
            .catch(() => 0),
        { timeout: 30_000 },
      )
      .toBe(200);
    // The dev status surface sits behind the profile gate — seed one via API.
    await request.post(`http://127.0.0.1:${newPort}/api/profile`, {
      headers: { Authorization: `Bearer ${newToken}` },
      data: { resume_markdown: "# E2E Candidate" },
    });

    // The page still holds the OLD (dead) handshake: the profile gate can't
    // resolve, so the app sits on the boot splash while the EventSource fails
    // against the dead port.
    await page.goto("/dev");
    await expect(page.getByTestId("boot-splash")).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: `${DIR}/dead-old-port.png`, fullPage: true });

    // Give the EventSource time to really fail against the dead port first,
    // then publish the restarted sidecar's handshake. The bus's failure-count
    // rebuild re-runs getSidecarInfo() and must pick this up; RealApi's own
    // per-request re-resolution recovers REST the same way.
    await page.waitForTimeout(2_000);
    await page.evaluate(
      ([port, token]) => {
        window.__FYJ_SIDECAR_INFO__ = { port: Number(port), token: String(token) };
      },
      [String(newPort), newToken],
    );

    await expect(page.getByTestId("sse-status")).toHaveText("live", {
      timeout: 30_000,
    });
    await expect(page.getByTestId("stream-reconnecting-banner")).toBeHidden();
    // REST recovered too: the operations ledger reads through the new port.
    await expect(page.getByTestId("ops-count")).toContainText(
      "operations recorded:",
    );
    await page.screenshot({ path: `${DIR}/recovered-new-port.png`, fullPage: true });
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

// Regression: the FAILED-handshake rebuild path. open()'s `.catch` calls
// scheduleRebuild() while `this.opening` is still set (`.finally` clears it
// only afterwards), so a `|| this.opening` clause in scheduleRebuild's guard
// meant a rejected re-handshake never armed the retry timer — the bus sat in
// "reconnecting" forever and F-M5's reconnect invalidation never fired. This
// test injects exactly that rejection: the e2e handshake seam is installed as
// a GETTER that throws once when read from the bus's ensureOpen frame (vite
// dev serves unminified code, so the frame name is stable) and serves the real
// info to every other caller. Until that one-shot throw is consumed the bus
// can only ever re-resolve the DEAD .env.local handshake, so it cannot reach
// "live" without first traversing handshake-rejection → scheduleRebuild →
// backoff retry — the exact path under test.
test("SSE stream recovers when the first re-handshake attempt fails", async ({
  page,
  request,
}) => {
  const old = sidecarInfo();
  let respawned: ChildProcess | null = null;

  try {
    // The previous test left every sidecar dead; be explicit anyway.
    await request
      .post(`http://127.0.0.1:${old.port}/shutdown`, {
        headers: { Authorization: `Bearer ${old.token}` },
      })
      .catch(() => undefined);

    const newPort = await freePort();
    const newToken = `e2e-failfirst-${Date.now()}`;
    respawned = spawnSidecar(newPort, newToken);
    await expect
      .poll(
        () =>
          request
            .get(`http://127.0.0.1:${newPort}/healthz`, {
              headers: { Authorization: `Bearer ${newToken}` },
            })
            .then((r) => r.status())
            .catch(() => 0),
        { timeout: 30_000 },
      )
      .toBe(200);
    await request.post(`http://127.0.0.1:${newPort}/api/profile`, {
      headers: { Authorization: `Bearer ${newToken}` },
      data: { resume_markdown: "# E2E Candidate" },
    });

    // Page boots against the dead .env.local handshake, as in the test above.
    await page.goto("/dev");
    await expect(page.getByTestId("boot-splash")).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Install the seam as a one-shot-failing getter (see header comment).
    await page.evaluate(
      ([port, token]) => {
        const w = window as unknown as Record<string, unknown>;
        w.__FYJ_BUS_HANDSHAKE_THROWS__ = 0;
        Object.defineProperty(window, "__FYJ_SIDECAR_INFO__", {
          configurable: true,
          get() {
            const stack = new Error().stack ?? "";
            if (stack.includes("ensureOpen") && w.__FYJ_BUS_HANDSHAKE_THROWS__ === 0) {
              w.__FYJ_BUS_HANDSHAKE_THROWS__ = 1;
              throw new Error("e2e: injected one-shot handshake failure");
            }
            return { port: Number(port), token: String(token) };
          },
        });
      },
      [String(newPort), newToken],
    );

    // First prove the injected rejection was actually consumed by the bus —
    // i.e. the failed-handshake path ran — then prove it recovered anyway.
    await expect
      .poll(
        () =>
          page.evaluate(
            () => (window as unknown as Record<string, unknown>).__FYJ_BUS_HANDSHAKE_THROWS__,
          ),
        { timeout: 30_000 },
      )
      .toBe(1);
    await expect(page.getByTestId("sse-status")).toHaveText("live", {
      timeout: 45_000,
    });
    await expect(page.getByTestId("stream-reconnecting-banner")).toBeHidden();
    await expect(page.getByTestId("ops-count")).toContainText(
      "operations recorded:",
    );
    await page.screenshot({
      path: `${DIR}/recovered-after-failed-handshake.png`,
      fullPage: true,
    });
  } finally {
    if (respawned?.pid) {
      try {
        process.kill(-respawned.pid, "SIGKILL");
      } catch {
        respawned.kill("SIGKILL");
      }
    }
  }
});
