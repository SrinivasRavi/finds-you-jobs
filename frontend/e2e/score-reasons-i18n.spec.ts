// Covers: a keyword score's reasons and factor evidence read in the user's
// language (audit section 5). The sidecar stores each line as a key and its
// facts; the pane words them, so switching to Spanish rewords a stored score.

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/score-reasons";

test.afterEach(async ({ request }) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/settings`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { thresholds: { scoring_mode: "llm" } },
  });
});

test("a keyword score's reasons follow the app's language", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: { thresholds: { scoring_mode: "keyword" } },
  });
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer. Python, SQL, AWS." },
  });
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "https://example.com/e2e-reasons-i18n",
        title: "Reasons Role",
        company: "Reasons Co",
        location: "Remote",
        description:
          "Backend engineer role. You will build and operate Python services, design " +
          "PostgreSQL schemas, run workloads on AWS, and review other engineers' work. " +
          "Requirements: strong Python, solid SQL, and production ownership experience.",
        source_adapter: "greenhouse",
      },
    })
  ).json();

  await page.goto("/jobs");
  await page.getByTestId("board-text-search").fill("reasons co");
  const row = page.locator('[data-testid="job-row"]', { hasText: "Reasons Role" });
  await expect(row.locator('[data-method="keyword"]')).toBeVisible({ timeout: 15_000 });
  await row.click();
  const reasons = page.getByTestId("match-reasons");
  await expect(reasons).toContainText("Skill-keyword overlap:");

  await page.goto("/settings");
  await page.getByTestId("settings-nav-appearance").click();
  await page.getByTestId("language-select").selectOption("es");
  await page.goto("/jobs");
  await page.getByTestId("board-text-search").fill("reasons co");
  await page.locator('[data-testid="job-row"]', { hasText: "Reasons Role" }).click();
  await expect(reasons).toContainText("Coincidencia de palabras clave:");
  await expect(reasons).not.toContainText("Skill-keyword overlap");
  await expect(page.getByText(/términos de la oferta aparecen en el CV/)).toBeVisible();
  await page.screenshot({ path: `${DIR}/es-keyword-reasons.png` });

  // Leave the shared e2e profile in English for every other spec.
  await page.goto("/settings");
  await page.getByTestId("settings-nav-appearance").click();
  await page.getByTestId("language-select").selectOption("en");
  await request.post(`${base}/api/jobs/${job.id}/tombstone`, { headers: auth });
});
