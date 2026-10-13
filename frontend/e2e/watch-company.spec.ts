// Covers: following a company's board (docs/internal/watch-company.md). The
// follow dialog strips a careers page's filters, reads the board, follows it even
// when the read fails, and never touches the job's own link. Settings › Discover
// jobs lists each link with Change link and Remove. Offline: every read fails.

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/watch-company";

test("follow a company from a job with no link, then manage it in Settings", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: {
      role_aliases: ["backend engineer"],
      locations: ["Remote"],
      portals_config: {
        sources: [{ url: "https://boards.greenhouse.io/e2e-nonexistent-tenant" }],
      },
    },
  });
  await request.post(`${base}/api/jobs`, {
    headers: auth,
    data: {
      canonical_url: "",
      title: "Follow Me Engineer",
      company: "E2E Follow Co",
      location: "Remote",
      description:
        "A pasted job description long enough to be a real posting. Python services, " +
        "PostgreSQL, AWS, mentoring, and production ownership across distributed systems.",
      source_adapter: "paste-text",
    },
  });

  // The job has no link, so the toggle asks for the careers page.
  await page.goto("/jobs");
  await page.getByText("Follow Me Engineer").first().click();
  await page.getByTestId("watch-company").click();
  await expect(page.getByTestId("follow-dialog")).toBeVisible();
  await page
    .getByTestId("follow-input")
    .fill("https://e2e-board.localhost/jobs.rss?location=Pune&utm_source=mail");
  await page.getByTestId("follow-submit").click();

  // Followed anyway, with the reason and the link, filters gone.
  const result = page.getByTestId("follow-result");
  await expect(result).toBeVisible({ timeout: 15_000 });
  await expect(result).toHaveAttribute("data-ok", "false");
  await expect(page.getByTestId("follow-link")).toHaveText("https://e2e-board.localhost/jobs.rss");
  await page.screenshot({ path: `${DIR}/01-followed-with-warning.png`, fullPage: true });
  await page.getByTestId("follow-done").click();

  // The job's own link was never touched.
  await expect(page.getByTestId("add-link-open")).toBeVisible();

  // The list lives in Settings › Discover jobs, each row showing its link.
  await page.goto("/settings");
  await page.getByTestId("settings-nav-discovery").click();
  const row = page.getByTestId("follow-row");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("E2E Follow Co");
  await expect(row).toContainText("https://e2e-board.localhost/jobs.rss");
  await page.getByTestId("follow-section").scrollIntoViewIfNeeded();
  // The rail's colours fade over 240 ms (url-less-jobs.md section 11.7).
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${DIR}/02-settings-list.png` });

  // Change link swaps the followed board for another.
  await page.getByTestId("follow-row-change").click();
  await page.getByTestId("follow-change-input").fill("https://e2e-board2.localhost/feed.rss");
  await page.getByTestId("follow-change-save").click();
  await expect(row).toHaveCount(1, { timeout: 15_000 });
  await expect(row).toContainText("https://e2e-board2.localhost/feed.rss");
  await expect(row).not.toContainText("https://e2e-board.localhost/jobs.rss");
  await page.screenshot({ path: `${DIR}/02b-link-changed.png` });

  // Remove takes it off the list.
  await page.getByTestId("follow-remove").click();
  await expect(row).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/03-removed.png`, fullPage: true });
});
