import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

// The e2e runs against the real browser-dev path: scripts/dev-web.mjs spawns
// the sidecar, writes frontend/.env.local, then starts vite, so the page talks to
// a live sidecar over loopback. FYJ_DATA_DIR points every sidecar, respawned ones
// included, at a throwaway dir so a run never touches real app data.
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

// The LinkedIn surface's origin is really linkedin.com, so the 4 specs that
// mount it skip unless this points at the loopback fixture. Defaulted here so a
// plain `npx playwright test` runs them; the port tracks FYJ_WEB_PORT, an explicit
// override wins, and an empty value counts as unset. Nothing may name the real origin.
if (!process.env.VITE_LINKEDIN_ORIGIN) {
  process.env.VITE_LINKEDIN_ORIGIN = `http://127.0.0.1:${WEB_PORT + 1000}/`;
}

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
    // FYJ_APPLY_DEV unlocks the apply op's dev knobs, so the applier e2e makes 0
    // model calls. FYJ_FAKE_LLM swaps every CLI engine for an instant fake; without it
    // each profile save ran a real `claude -p` (2026-07-18). FYJ_DATA_DIR honours a
    // caller's override but always lands on a throwaway dir.
    env: {
      FYJ_DATA_DIR: process.env.FYJ_DATA_DIR ?? E2E_DATA_DIR,
      FYJ_APPLY_DEV: "1",
      FYJ_FAKE_LLM: "1",
      // A fixed throwaway Fernet key: a saved API key in a spec never reaches
      // the OS keychain, which can open a prompt and block the sidecar.
      FYJ_SESSION_KEY: "ZTJlLW9ubHktdGhyb3dhd2F5LWtleS0wMDAwMDAwMDA=",
      // vite bakes VITE_* at serve time, so the spawned dev-web needs the
      // fixture origin the specs above default.
      VITE_LINKEDIN_ORIGIN: process.env.VITE_LINKEDIN_ORIGIN!,
    },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
