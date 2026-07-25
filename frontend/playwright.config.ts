import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

// The e2e runs against the real sidecar-backed browser-dev path:
// `scripts/dev-web.mjs` spawns the sidecar (PORT/TOKEN handshake), writes
// frontend/.env.local, then starts vite — so the page under test talks to a
// live sidecar over loopback, exactly like the shell-managed runtime.
//
// FYJ_DATA_DIR points the sidecar (and any sidecar a spec respawns — see
// e2e/reconnect.spec.ts) at a throwaway dir under e2e/_results so test runs
// never touch the developer's real app-data.
export const E2E_DATA_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "e2e",
  "_results",
  "appdata",
);

// FYJ_WEB_PORT (default 1420): run the e2e stack on a second port while a
// `pnpm dev` session holds 1420 — vite.config.ts reads the same variable, so
// the spawned dev-web vite and the URLs here stay in lockstep. Without this,
// reuseExistingServer would attach the tests to the developer's live session.
const WEB_PORT = Number(process.env.FYJ_WEB_PORT ?? 1420);

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/_results",
  fullyParallel: false,
  // One worker: the reconnect spec kills and respawns the shared sidecar —
  // a parallel board test would see the outage as flakiness.
  workers: 1,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "off",
  },
  webServer: {
    command: "node ../scripts/dev-web.mjs",
    url: `http://127.0.0.1:${WEB_PORT}`,
    reuseExistingServer: true,
    timeout: 90_000,
    // TERM first so dev-web's own cleanup (child process groups, .env.local)
    // runs before Playwright escalates to a group SIGKILL.
    gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    // FYJ_APPLY_DEV unlocks the apply op's dev knobs (scripted engine, local
    // fixture URLs, headless) so the applier e2e runs with zero model calls
    // and zero external traffic — same seam the sidecar tests use.
    // FYJ_FAKE_LLM swaps every builtin CLI engine for an instant fake: without
    // it, each spec's profile save enqueued an `extract` op that ran a REAL
    // `claude -p` on the dev machine's subscription (2026-07-18 finding) —
    // real tokens, ~10s child subprocesses, shutdown-drain flakes.
    env: { FYJ_DATA_DIR: E2E_DATA_DIR, FYJ_APPLY_DEV: "1", FYJ_FAKE_LLM: "1" },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
