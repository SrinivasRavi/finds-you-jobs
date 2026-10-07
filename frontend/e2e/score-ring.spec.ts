// Covers: the 2-ring score dial (maintainer, 2026-10-07). The outer arc is the
// score on a continuous red-to-green ramp; the inner ring is the method that
// produced it. A board can now carry 4 methods' numbers at once, so a card has
// to say which one it is without being opened.
//
// The 4 methods can't all be produced for real in e2e (Fast scoring needs an
// OpenRouter key, On-device has no pack yet), so the board response is patched
// the way board.spec.ts already patches it for the 150-row virtualization test.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/score-ring";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

const METHODS = [
  { method: "llm", score: 91, title: "Staff Backend Engineer", company: "Stripe" },
  { method: "jev", score: 74, title: "Platform Engineer", company: "Ramp" },
  { method: "local", score: 58, title: "Services Engineer", company: "Linear" },
  { method: "keyword", score: 31, title: "Frontend Engineer", company: "Vercel" },
];

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("every scoring method is legible on the board and in the detail pane", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  await request.post(`${base}/api/jobs`, {
    headers: auth,
    data: {
      canonical_url: "https://example.com/e2e-ring-seed",
      title: "Seed Role",
      company: "Seed",
      location: "Remote",
      description:
        "Backend engineer role. You will build and operate Python services, design " +
        "PostgreSQL schemas, run workloads on AWS, and review other engineers' work. " +
        "Requirements: strong Python, solid SQL, and production ownership experience.",
      source_adapter: "greenhouse",
    },
  });

  await page.route("**/api/board*", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as {
      jobs: Record<string, unknown>[];
      total: number;
    };
    const template = body.jobs[0];
    if (!template) throw new Error("the seeded job never reached the board");
    body.jobs = METHODS.map((m, i) => ({
      ...template,
      id: `ring-${m.method}`,
      title: m.title,
      company: m.company,
      canonical_url: `https://example.com/e2e-ring-${i}`,
      scoreStatus: "scored",
      score: {
        ...(template.score as Record<string, unknown>),
        score_0_100: m.score,
        method: m.method,
        scorer_impl: m.method === "keyword" ? "scorer-deterministic" : "scorer-llm",
      },
    }));
    body.total = body.jobs.length;
    await route.fulfill({ response, json: body });
  });

  await page.goto("/jobs");
  const rows = page.locator('[data-testid="job-row"]');
  await expect(rows).toHaveCount(METHODS.length, { timeout: 15_000 });

  // The list: each row's number carries its method's colour.
  for (const m of METHODS) {
    await expect(page.locator(`[data-method="${m.method}"]`).first()).toBeVisible();
  }
  await page.screenshot({ path: `${DIR}/board-4-methods.png`, fullPage: true });

  // The detail pane: the dial itself, one method at a time.
  for (const m of METHODS) {
    await rows.filter({ hasText: m.company }).first().click();
    const dial =
      m.method === "keyword"
        ? page.locator('[data-testid="keyword-score-ring"]')
        : page.locator('[data-testid="match-score-ring"]');
    await expect(dial).toBeVisible({ timeout: 10_000 });
    await expect(dial).toHaveAttribute("data-method", m.method);
    await expect(dial).toContainText(String(m.score));
    await dial.screenshot({ path: `${DIR}/dial-${m.method}.png` });
  }

  // And the whole pane, so the dial is reviewable in its real surroundings.
  await page.screenshot({ path: `${DIR}/detail-with-dial.png`, fullPage: true });
});
