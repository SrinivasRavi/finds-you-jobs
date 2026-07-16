// Covers: skeleton cold boot — the UI connects to the live sidecar over the
// PORT/TOKEN handshake and renders an honest "connected" state.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}


const DIR = "e2e/_screenshots/boot";

test("shell skeleton reaches a healthy sidecar", async ({ page, request }) => {
  // The dev status surface sits behind the profile gate — seed one via API.
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate" },
  });
  await page.goto("/dev");

  await expect(page.getByTestId("sidecar-status")).toHaveText("connected", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("sidecar-port")).toContainText(
    "sidecar healthy on 127.0.0.1:",
  );

  await page.screenshot({ path: `${DIR}/connected.png`, fullPage: true });
});
