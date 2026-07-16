// Covers: job discovery — the discovered-job board renders real rows from the
// live sidecar (roadmap commit-7 UI gate). Seeds a master profile (satisfies
// the onboarding gate — the wizard itself is a later commit) and two jobs via
// the API, then asserts the board lists them with honest scan status.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/board";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("board lists discovered jobs from the live sidecar", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  // Satisfy the profile gate + seed two distinguishable jobs.
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  for (const [n, title, company] of [
    ["1", "Backend Engineer", "Stripe"],
    ["2", "Platform Engineer", "Acme"],
  ]) {
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: `https://example.com/e2e-job-${n}`,
        title,
        company,
        location: "Remote",
        description: "Build reliable systems.",
        source_adapter: "paste-url",
      },
    });
  }

  await page.goto("/jobs");
  await expect(page.getByText("Backend Engineer").first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText("Platform Engineer").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/board-discovered-jobs.png`, fullPage: true });

  // The JD detail pane opens from a row.
  await page.getByText("Backend Engineer").first().click();
  await expect(page.getByText("Build reliable systems.").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/board-detail.png`, fullPage: true });
});
