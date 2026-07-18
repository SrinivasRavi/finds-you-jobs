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

test("provider step offers the subscription-CLI family beside BYOK", async ({ page }) => {
  await page.goto("/onboarding");
  // Step 0 → 1: paste a resume.
  await page.getByTestId("resume-text").fill("# E2E Candidate\n\nBackend engineer.");
  await page.getByTestId("onboarding-continue").click();
  // Step 1 → 2: one alias + one location.
  await page.getByTestId("alias-input").fill("Backend Engineer");
  await page.getByTestId("alias-input").press("Enter");
  await page.getByTestId("location-input").fill("Remote");
  await page.getByTestId("location-input").press("Enter");
  await page.getByTestId("onboarding-continue").click();
  await expect(page.getByTestId("onboarding-step-2")).toBeVisible();

  // The full provider menu: 4 BYOK + 3 subscription CLIs.
  for (const id of [
    "openrouter",
    "local",
    "anthropic",
    "openai",
    "claude-cli",
    "codex-cli",
    "antigravity-cli",
  ]) {
    await expect(page.getByTestId(`provider-${id}`)).toBeVisible();
  }

  // A CLI selection swaps the key input for the no-key hint (verify-only path).
  await page.getByTestId("provider-codex-cli").click();
  await expect(page.getByTestId("api-key")).toHaveCount(0);
  await expect(page.getByText("we verify your Codex CLI is logged in")).toBeVisible();
  // Exactly one tile is selected (accent-wash) — and wait out the global
  // 240ms theme-fade transition (index.css) so the screenshot shows settled
  // colors, not the previous tile mid-fade.
  await expect(page.getByTestId("provider-codex-cli")).toHaveClass(/bg-accent-wash/);
  await expect(page.getByTestId("provider-openrouter")).not.toHaveClass(/bg-accent-wash/);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/onboarding-step2-codex-cli.png`, fullPage: true });

  await page.getByTestId("provider-antigravity-cli").click();
  await expect(page.getByText(/Experimental: agy's non-interactive mode/)).toBeVisible();
  await expect(page.getByTestId("provider-antigravity-cli")).toHaveClass(/bg-accent-wash/);
  await expect(page.getByTestId("provider-codex-cli")).not.toHaveClass(/bg-accent-wash/);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/onboarding-step2-antigravity-cli.png`, fullPage: true });
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
