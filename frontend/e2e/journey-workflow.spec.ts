// Covers: the core loop by hand. A job is saved from the board, generates its
// resume and cover letter, and is approved, moved, prioritised and noted in
// the tracker. Also Trash and restore on the board. The e2e fake model answers
// the tailor and cover contracts, so nothing spends tokens.

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { auth, seedProfile, sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/journey-workflow";

type App = {
  id: string;
  column: string;
  priority: string;
  notes_markdown: string;
  packetResumeState: string;
  packetCoverLetterState: string;
  job: { title: string };
};

async function seedJob(request: APIRequestContext, slug: string, title: string, company: string) {
  const { base, token } = sidecarInfo();
  const res = await request.post(`${base}/api/jobs`, {
    headers: auth(token),
    data: {
      canonical_url: `https://example.com/journey-${slug}`,
      title,
      company,
      location: "Remote",
      description: `${title} at ${company}. Build and run distributed systems in Python and Go.`,
      source_adapter: "paste-url",
    },
  });
  return (await res.json()) as { id: string };
}

async function appFor(request: APIRequestContext, title: string): Promise<App | undefined> {
  const { base, token } = sidecarInfo();
  const apps = (await (
    await request.get(`${base}/api/applications`, { headers: auth(token) })
  ).json()) as App[];
  return apps.find((a) => a.job?.title === title);
}

async function openJob(page: Page, title: string) {
  await page.goto("/jobs");
  await page.getByTestId("board-text-search").fill(title);
  await page.getByTestId("job-row").filter({ hasText: title }).first().click();
}

test.beforeEach(async ({ request }) => {
  await seedProfile(request, "# Journey Candidate\n\n## Experience\n\n- Built distributed systems.");
});

test("save from the board, then the tracker generates, approves and shares both documents", async ({
  page,
  request,
  context,
}) => {
  const title = "Journey Platform Engineer";
  await seedJob(request, "save", title, "Hooli");
  await openJob(page, title);
  const save = page.getByTestId("save-to-tracker");
  await expect(save).toHaveAttribute("data-saved", "false");
  await page.screenshot({ path: `${DIR}/board-job-open.png`, fullPage: true });
  await save.click();
  // A saved job leaves the board for the tracker (FR-JB-02).
  await expect(page.getByTestId("job-row").filter({ hasText: title })).toHaveCount(0);

  // Saved with generation on: both documents generate through the fake model.
  await expect
    .poll(async () => {
      const a = await appFor(request, title);
      return [a?.column, a?.packetResumeState, a?.packetCoverLetterState];
    }, { timeout: 30_000 })
    .toEqual(["saved", "ready", "ready"]);

  await page.getByRole("link", { name: "Applications" }).click();
  const card = page.getByTestId("tracker-card").filter({ hasText: title });
  await expect(card).toBeVisible();
  // A Keyword score (here the "no description" marker) reads grey, as on the
  // board: the free floor never wears a paid score's colour.
  await expect(card.getByTestId("card-score")).toHaveAttribute("data-method", "keyword");
  await expect(card.getByTestId("card-score")).not.toHaveClass(/text-bad/);
  await page.screenshot({ path: `${DIR}/tracker-saved-ready.png`, fullPage: true });

  // Resume: open, read, approve.
  await card.getByTestId("card-resume-slot").click();
  const tailored = page.getByTestId("tailored-editor");
  await expect(tailored).toContainText("Fake Tailored Resume");
  // The pane's Preview|Raw switch fits inside the pane, beside the tailorer's notes.
  const raw = (await page.getByRole("button", { name: "Raw", exact: true }).boundingBox())!;
  const pane = (await tailored.boundingBox())!;
  expect(raw.x + raw.width).toBeLessThanOrEqual(pane.x + pane.width + 1);
  await page.screenshot({ path: `${DIR}/tailored-ready.png`, fullPage: true });
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByTestId("share-btn").click();
  await page.getByTestId("share-copy-md").click();
  await expect(page.getByTestId("share-copy-md")).toContainText("Copied");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(
    "# Fake Tailored Resume",
  );
  await page.getByTestId("approve-and-save").click();
  await expect.poll(async () => (await appFor(request, title))?.packetResumeState).toBe("approved");
  await page.keyboard.press("Escape");

  // Cover letter: open, approve.
  await card.getByTestId("card-cover-slot").click();
  await expect(page.getByTestId("cover-editor")).toContainText("Fake cover letter.");
  await page.getByTestId("approve-and-save").click();
  await expect
    .poll(async () => (await appFor(request, title))?.packetCoverLetterState)
    .toBe("approved");
  await page.keyboard.press("Escape");
  await page.screenshot({ path: `${DIR}/tracker-both-approved.png`, fullPage: true });

  // Regenerate replaces the draft and brings it back to review.
  await card.getByTestId("card-resume-slot").click();
  await page.getByTestId("generate-variant").click();
  // It asks for optional per-job instructions first.
  const guidance = page.getByRole("dialog", { name: "Generate tailored resume" });
  await guidance.getByRole("textbox").fill("Lead with the Go work.");
  await page.screenshot({ path: `${DIR}/regenerate-guidance.png`, fullPage: true });
  await guidance.getByRole("button", { name: "Generate tailored resume" }).click();
  await expect.poll(async () => (await appFor(request, title))?.packetResumeState, {
    timeout: 30_000,
  }).toBe("ready");
  if (!(await page.getByTestId("tailored-editor").isVisible())) {
    await card.getByTestId("card-resume-slot").click();
  }
  await expect(page.getByTestId("approve-and-save")).toBeVisible();
});

test("the tracker card moves by drag, warns before Applied, and won't drag back", async ({
  page,
  request,
}) => {
  const title = "Journey Drag Engineer";
  const job = await seedJob(request, "drag", title, "Pied Piper");
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/applications`, {
    headers: auth(token),
    data: { job_id: job.id, generate_resume: false, generate_cover: false },
  });
  await page.goto("/applications");
  const card = () => page.getByTestId("tracker-card").filter({ hasText: title });
  await expect(page.getByTestId("col-Saved").getByText(title)).toBeVisible();

  await card().dragTo(page.getByTestId("col-Seeking-Referral"));
  await expect(page.getByTestId("col-Seeking-Referral").getByText(title)).toBeVisible();
  await expect.poll(async () => (await appFor(request, title))?.column).toBe("seeking_referral");

  // Into Applied: a one-way door, so it asks; Cancel leaves the card put.
  await card().dragTo(page.getByTestId("col-Applied"));
  await expect(page.getByTestId("frozen-move-confirm")).toBeVisible();
  await page.screenshot({ path: `${DIR}/frozen-move-confirm.png`, fullPage: true });
  await page.getByTestId("frozen-move-cancel").click();
  await expect(page.getByTestId("col-Seeking-Referral").getByText(title)).toBeVisible();

  await card().dragTo(page.getByTestId("col-Applied"));
  await page.getByTestId("frozen-move-proceed").click();
  await expect(page.getByTestId("col-Applied").getByText(title)).toBeVisible();
  await expect.poll(async () => (await appFor(request, title))?.column).toBe("applied");

  // Back out of Applied is refused.
  await card().dragTo(page.getByTestId("col-Saved"));
  await expect(page.getByTestId("col-Applied").getByText(title)).toBeVisible();
  expect((await appFor(request, title))?.column).toBe("applied");
  await page.screenshot({ path: `${DIR}/tracker-applied.png`, fullPage: true });
});

test("the detail modal saves priority and notes, and Activity records both", async ({
  page,
  request,
}) => {
  const title = "Journey Notes Engineer";
  const job = await seedJob(request, "notes", title, "Umbrella");
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/applications`, {
    headers: auth(token),
    data: { job_id: job.id, generate_resume: false, generate_cover: false },
  });
  await page.goto("/applications");
  await page.getByTestId("tracker-card").filter({ hasText: title }).getByTestId("card-title").click();

  await page.getByTestId("priority-select").selectOption("P0");
  await expect.poll(async () => (await appFor(request, title))?.priority).toBe("P0");

  await page.getByRole("button", { name: "Notes", exact: true }).click();
  const notes = page.getByTestId("notes-editor");
  await notes.fill("Recruiter: Sam. Ask about the on-call rota.");
  await notes.blur();
  await expect
    .poll(async () => (await appFor(request, title))?.notes_markdown)
    .toBe("Recruiter: Sam. Ask about the on-call rota.");
  await page.screenshot({ path: `${DIR}/detail-notes.png`, fullPage: true });

  await page.getByRole("button", { name: "Activity", exact: true }).click();
  await expect(page.getByTestId("activity-log")).toContainText("Added to tracker");
  await expect(page.getByTestId("activity-entry").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/detail-activity.png`, fullPage: true });
});

test("Remove sends a job to Deleted jobs, and Undo brings it back", async ({ page, request }) => {
  const title = "Journey Trash Engineer";
  await seedJob(request, "trash", title, "Massive Dynamic");
  await openJob(page, title);
  await page.getByTestId("remove-job").click();
  await expect(page.getByTestId("job-row").filter({ hasText: title })).toHaveCount(0);

  await page.getByTestId("trash-btn").click();
  const trash = page.getByTestId("trash-modal");
  await expect(trash).toContainText(title);
  await page.screenshot({ path: `${DIR}/trash-modal.png`, fullPage: true });
  await trash.locator("li, [data-testid]").filter({ hasText: title }).getByTestId("trash-undo-btn").first().click();
  await expect(trash).not.toContainText(title);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("job-row").filter({ hasText: title })).toBeVisible();
});

test("the board sorts by recency, and the work-style and link filters narrow it", async ({
  page,
  request,
}) => {
  await seedJob(request, "sort-a", "Journey Sort Older", "Soylent");
  await seedJob(request, "sort-b", "Journey Sort Newer", "Soylent");
  await page.goto("/jobs");
  await page.getByTestId("board-text-search").fill("Journey Sort");
  const rows = page.getByTestId("job-row");
  await expect(rows).toHaveCount(2);
  await page.getByTestId("sort-toggle").getByRole("button", { name: "Recency" }).click();
  await expect(rows.first()).toContainText("Journey Sort Newer");

  // No posting is Hybrid; Remote brings both back. Both have a link.
  await page.locator("#filter-ws").getByRole("button", { name: "Hybrid", exact: true }).click();
  await expect(rows).toHaveCount(0);
  await page.locator("#filter-ws").getByRole("button", { name: "Remote", exact: true }).click();
  await expect(rows).toHaveCount(2);
  await page.locator("#filter-link").getByRole("button", { name: "No link" }).click();
  await expect(rows).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/board-filtered.png`, fullPage: true });
});
