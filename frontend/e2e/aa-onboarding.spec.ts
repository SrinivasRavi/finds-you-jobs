// Covers: the onboarding gate + wizard (US-OB-*) against the live sidecar.
// Named `aa-` so it runs FIRST in the suite — the gate redirect is only
// observable while the appdata has no master profile, and later specs seed
// one. Completes the resume step via paste (the same persistence path the
// upload uses after review), then verifies the gate flips.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/onboarding";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

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

// S-C54, S-C55: the resume step says what a master resume is and lists every
// format the uploader takes, and the 12 other languages say the same.
test("the resume step explains the master resume, in every language", async ({ page }) => {
  await page.goto("/onboarding");
  await expect(page.getByTestId("onboarding-stepper")).toContainText("Master resume");
  await expect(page.getByTestId("onboarding-step-0")).toContainText(
    "Your master resume is your resume with everything in it",
  );
  await expect(page.getByTestId("onboarding-step-0")).toContainText(
    "PDF, Word, Pages, ODT, RTF, Markdown or text",
  );
  await page.screenshot({ path: `${DIR}/resume-step-en.png`, fullPage: true });

  for (const [lang, phrase] of [
    ["de", "Master-Lebenslauf hochladen: PDF, Word"],
    ["ja", "マスター職務経歴書をアップロード"],
  ] as const) {
    await page.evaluate((l) => localStorage.setItem("fyj-language", l), lang);
    await page.reload();
    await expect(page.getByTestId("onboarding-step-0")).toContainText(phrase, { timeout: 10_000 });
    await expect(page.getByTestId("onboarding-step-0")).not.toContainText(".md / .txt / .pdf");
    await page.screenshot({ path: `${DIR}/resume-step-${lang}.png`, fullPage: true });
  }
  await page.evaluate(() => localStorage.removeItem("fyj-language"));
});

// S-C56: an uploaded resume opens rendered, so the user can see the
// extraction came out right; Raw shows the markdown.
test("an uploaded resume shows as a rendered preview", async ({ page }) => {
  await page.goto("/onboarding");
  await page.getByTestId("resume-upload").setInputFiles({
    name: "resume.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Ada Lovelace\n\n## Experience\n\n- Built the **Analytical Engine** notes\n"),
  });
  const preview = page.getByTestId("resume-preview");
  await expect(preview.locator("h1")).toHaveText("Ada Lovelace", { timeout: 15_000 });
  await expect(preview.locator("strong")).toHaveText("Analytical Engine");
  await page.screenshot({ path: `${DIR}/resume-preview.png`, fullPage: true });
  await page.getByTestId("mode-raw").click();
  await expect(page.getByTestId("resume-text")).toHaveValue(/# Ada Lovelace/);
});

// S-C57: a file that isn't a resume gets a plain warning, never a block.
test("an upload that isn't a resume is flagged, and the user can continue", async ({ page }) => {
  await page.goto("/onboarding");
  await page.getByTestId("resume-upload").setInputFiles({
    name: "lease.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("This lease agreement is made between the landlord and the tenant. ".repeat(3)),
  });
  await expect(page.getByTestId("resume-warning-not_a_resume")).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${DIR}/resume-warning.png`, fullPage: true });
  await expect(page.getByTestId("onboarding-continue")).toBeEnabled();
});

// S-C58: a role typed but not yet a chip counts, so Continue isn't greyed out.
test("a typed role is enough to continue", async ({ page }) => {
  await page.goto("/onboarding");
  await page.getByTestId("resume-text").fill("# E2E Candidate\n\nBackend engineer.");
  await page.getByTestId("onboarding-continue").click();
  await page.getByTestId("location-input").fill("Remote");
  await page.getByTestId("location-input").press("Enter");
  await page.getByTestId("alias-input").fill("Forward Deployed Engineer");
  await expect(page.getByTestId("onboarding-continue")).toBeEnabled();
  await page.screenshot({ path: `${DIR}/typed-role-continue.png`, fullPage: true });
  await page.getByTestId("onboarding-continue").click();
  await expect(page.getByTestId("onboarding-step-2")).toBeVisible();
  // It was kept as a chip, not dropped.
  await page.getByText("Back").click();
  await expect(page.getByTestId("alias")).toContainText("Forward Deployed Engineer");
});

// S-C64, S-C65: the last step says what happens in a line, the button says
// "Find jobs", and Back is there.
test("the All set step is short, and Back works from it", async ({ page }) => {
  await page.goto("/onboarding");
  await page.evaluate(() =>
    localStorage.setItem(
      "fyj-onboarding-draft-v1",
      JSON.stringify({
        step: 3,
        resumeText: "# E2E Candidate\n\nBackend engineer.",
        aliases: ["Backend Engineer"],
        locations: ["Remote"],
      }),
    ),
  );
  await page.reload();
  await expect(page.getByTestId("onboarding-step-3")).toContainText("You're all set");
  await expect(page.getByTestId("onboarding-finish")).toHaveText("Find jobs");
  await page.screenshot({ path: `${DIR}/all-set.png`, fullPage: true });
  await page.getByText("Back").click();
  await expect(page.getByTestId("onboarding-step-2")).toBeVisible();
  await page.evaluate(() => localStorage.removeItem("fyj-onboarding-draft-v1"));
});

test("provider step offers the subscription-CLI family beside BYOK", async ({ page }) => {
  await page.goto("/onboarding");
  // Step 0 → 1: paste a resume.
  await page.getByTestId("resume-text").fill("# E2E Candidate\n\nBackend engineer.");
  await page.getByTestId("onboarding-continue").click();
  // Step 1 → 2: one alias + one location.
  await page.getByTestId("alias-input").fill("Backend Engineer");
  await page.getByTestId("alias-input").press("Enter");
  // The location picker (place-picker.md): "London" offers both, named in full.
  await page.getByTestId("location-input").fill("London");
  await expect(page.getByTestId("location-option").nth(1)).toHaveText("London, Ontario, Canada");
  await page.getByTestId("location-input").press("ArrowDown");
  await page.getByTestId("location-input").press("Enter");
  await page.getByTestId("location-input").fill("Remote");
  await page.getByTestId("location-input").press("Enter");
  await expect(page.getByTestId("location")).toContainText("London, Ontario, Canada");
  await expect(page.getByTestId("location")).toContainText("Remote");
  await page.screenshot({ path: "e2e/_screenshots/onboarding/onboarding-step1-places.png" });
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
  // S-C61: the hint says Verify reads which account is logged in.
  await expect(
    page.getByText("Verify asks the Codex CLI whether you're logged in"),
  ).toBeVisible();
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

// S-C60: Skip on the provider step gets the user in with AI off, says so,
// and leaves keyword scoring on; Settings then says how to turn AI on.
test("skipping the provider step finishes with AI off", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  // The cold-start scan reads 1 dead board, not the seeded 315.
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: { portals_config: { sources: [{ url: "https://boards.greenhouse.io/e2e-nonexistent-tenant" }] } },
  });
  await page.goto("/onboarding");
  await page.getByTestId("resume-text").fill("# E2E Candidate\n\nBackend engineer.");
  await page.getByTestId("onboarding-continue").click();
  await page.getByTestId("alias-input").fill("Backend Engineer");
  await page.getByTestId("alias-input").press("Enter");
  await page.getByTestId("location-input").fill("Remote");
  await page.getByTestId("location-input").press("Enter");
  await page.getByTestId("onboarding-continue").click();
  await expect(page.getByTestId("onboarding-continue")).toBeDisabled();
  await page.getByTestId("onboarding-skip-provider").click();
  await expect(page.getByTestId("all-set-ai-off")).toBeVisible();
  await page.screenshot({ path: `${DIR}/all-set-ai-off.png`, fullPage: true });
  // The Skip survives a reload with the rest of the draft (PR #12 review).
  await page.reload();
  await expect(page.getByTestId("all-set-ai-off")).toBeVisible();
  const extracts = async () =>
    (await (await request.get(`${base}/api/operations?limit=200`, { headers: auth })).json())
      .filter((o: { kind: string }) => o.kind === "extract").length;
  const extractsBefore = await extracts();
  await page.getByTestId("onboarding-finish").click();
  await expect(page).toHaveURL(/\/jobs$/, { timeout: 15_000 });
  // AI is off before the resume is saved, so no resume read is queued (audit A5).
  expect(await extracts()).toBe(extractsBefore);

  const settings = await (await request.get(`${base}/api/settings`, { headers: auth })).json();
  const engines = Object.values(settings.preferences.engine_routing).map(
    (r) => (r as { engine: string }).engine,
  );
  expect(new Set(engines)).toEqual(new Set(["none"]));
  expect(settings.preferences.thresholds.scoring_mode).toBe("keyword");

  await page.goto("/settings");
  await expect(page.getByTestId("ai-off-warning")).toBeVisible({ timeout: 10_000 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${DIR}/settings-ai-off.png`, fullPage: true });

  // Back to the default routing and LLM scoring, for the specs after this one.
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: {
      engine_routing: {},
      thresholds: { ...settings.preferences.thresholds, scoring_mode: "llm" },
    },
  });
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
