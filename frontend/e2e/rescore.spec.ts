// Covers: Rescore on a board row and the rescore-due mark (audit A3,
// score-storage.md 4.4 and 5.2). The click runs for real in keyword mode, so it
// spends nothing; the mark is drawn from a patched board row, because the
// staleness rule itself is pinned in test_job_score_factors.py.

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/rescore";

test("a row rescores on request and shows when its score predates a major resume edit", async ({
  page,
  request,
}) => {
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
        canonical_url: "https://example.com/e2e-rescore",
        title: "Rescore Role",
        company: "Rescore Co",
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
  const row = page.locator('[data-testid="job-row"]', { hasText: "Rescore Role" });
  await expect(row.locator('[data-method="keyword"]')).toBeVisible({ timeout: 15_000 });
  const before = (
    await (await request.get(`${base}/api/operations?limit=200`, { headers: auth })).json()
  ).filter((o: { kind: string }) => o.kind === "score").length;

  await row.getByTestId("rescore-row").click();
  // Pending for as long as the operation waits in its queue and runs.
  await expect(row.getByTestId("rescore-row")).toHaveText("Rescoring…");
  await expect(row.getByTestId("rescore-row")).toHaveText("Rescore", { timeout: 60_000 });
  const ops = (
    await (await request.get(`${base}/api/operations?limit=200`, { headers: auth })).json()
  ).filter((o: { kind: string }) => o.kind === "score");
  expect(ops.length).toBe(before + 1);
  const mine = ops.find(
    (o: { input_snapshot: { job_id?: string; rescore?: boolean } }) =>
      o.input_snapshot.job_id === job.id && o.input_snapshot.rescore,
  );
  expect(mine.requested_by).toBe("user");
  expect(mine.state).toBe("succeeded");
  await page.screenshot({ path: `${DIR}/rescored-row.png` });

  await page.route("**/api/board*", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { jobs: Record<string, unknown>[] };
    for (const j of body.jobs) {
      if (j.id === job.id) (j.score as Record<string, unknown>).stale = true;
    }
    await route.fulfill({ response, json: body });
  });
  await page.reload();
  const stale = page
    .locator('[data-testid="job-row"]', { hasText: "Rescore Role" })
    .getByTestId("score-stale");
  await expect(stale).toBeVisible({ timeout: 15_000 });
  await expect(stale).toHaveAttribute("title", /major edit to your resume/);
  await page.screenshot({ path: `${DIR}/stale-mark.png` });
});
