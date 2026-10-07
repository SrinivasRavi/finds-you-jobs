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

test("the factor bars draw a confidence differently from a graded position", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });

  // Jev's real axis set: 5 graded positions and 2 confidences, with the weight
  // table its own total used. The confidences carry no weight on purpose.
  await page.route("**/api/jobs/*/factors", async (route) => {
    await route.fulfill({
      json: {
        job_id: "ring-jev",
        method: "jev",
        weights: {
          hard_requirements: 0.3,
          core_stack: 0.3,
          years: 0.15,
          level: 0.15,
          nice_to_haves: 0.1,
        },
        factors: [
          { factor: "hard_requirements", value: 1.0, kind: "graded",
            evidence: "Every item the posting states as required is evidenced in the resume." },
          { factor: "core_stack", value: 0.9, kind: "graded",
            evidence: "Hands-on with most of the core stack, with one adjacent substitution." },
          { factor: "years", value: 1.0, kind: "graded",
            evidence: "9 on the resume against 6 required" },
          { factor: "level", value: 0.95, kind: "graded",
            evidence: "Senior stated, senior evidenced" },
          { factor: "nice_to_haves", value: 0.25, kind: "graded",
            evidence: "Go is named as nice to have and is absent" },
          { factor: "location_preference", value: 0.96, kind: "probability",
            evidence: "96% confidence the posting is in a location you asked for" },
          { factor: "logistics", value: 0.96, kind: "probability",
            evidence: "4% confidence the posting states a constraint your resume contradicts" },
        ],
      },
    });
  });

  await page.goto("/jobs");
  const row = page.locator('[data-testid="job-row"]').first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();

  const bars = page.locator('[data-testid="factor-bars"]');
  await expect(bars).toBeVisible({ timeout: 10_000 });

  // Rule 1: a confidence is drawn differently and is labelled as one.
  await expect(page.locator('[data-testid="factor-location_preference"]')).toHaveAttribute(
    "data-kind",
    "probability",
  );
  // Rule 2: a graded axis shows its weight, a confidence never does.
  await expect(page.locator('[data-testid="weight-core_stack"]')).toBeVisible();
  await expect(page.locator('[data-testid="weight-location_preference"]')).toHaveCount(0);
  // Rule 3: the evidence is on screen, not only on hover.
  await expect(bars).toContainText("9 on the resume against 6 required");

  await bars.screenshot({ path: `${DIR}/factor-bars.png` });
  await page.screenshot({ path: `${DIR}/detail-with-bars.png`, fullPage: true });
});
