import { defineConfig, devices } from "@playwright/test";

// The skeleton e2e runs against the real sidecar-backed browser-dev path:
// `scripts/dev-web.mjs` spawns the sidecar (PORT/TOKEN handshake), writes
// frontend/.env.local, then starts vite — so the page under test talks to a
// live sidecar over loopback, exactly like the shell-managed runtime.
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
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
