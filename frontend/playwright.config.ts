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

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/_results",
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:1420",
    trace: "off",
  },
  webServer: {
    command: "node ../scripts/dev-web.mjs",
    url: "http://127.0.0.1:1420",
    reuseExistingServer: true,
    timeout: 90_000,
    env: { FYJ_DATA_DIR: E2E_DATA_DIR },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
