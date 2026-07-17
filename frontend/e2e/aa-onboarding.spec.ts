// Covers: the onboarding gate + wizard (US-OB-*) against the live sidecar.
// Named `aa-` so it runs FIRST in the suite — the gate redirect is only
// observable while the appdata has no master profile, and later specs seed
// one. Completes the resume step via paste (the same persistence path the
// upload uses after review), then verifies the gate flips.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/onboarding";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("fresh install gates every route to /onboarding and renders the wizard", async ({
  page,
}) => {
  await page.goto("/jobs");
  await expect(page).toHaveURL(/\/onboarding$/, { timeout: 15_000 });
  await expect(page.getByTestId("onboarding-stepper")).toBeVisible();
  await expect(page.getByTestId("onboarding-step-0")).toBeVisible();
  await expect(page.getByTestId("resume-text")).toBeVisible();
  await page.screenshot({ path: `${DIR}/onboarding-step0.png`, fullPage: true });
});

test("onboarded install redirects /onboarding back to the board", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  await page.goto("/onboarding");
  await expect(page).toHaveURL(/\/jobs$/, { timeout: 15_000 });
});
