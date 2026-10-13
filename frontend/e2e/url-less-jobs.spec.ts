// Covers: a job with no posting URL, through the real surfaces
// (docs/internal/url-less-jobs.md). Not a gallery — every step here is the
// door a user actually reaches: the board header's Add a job, the card's
// chips, the filter row's Link group, the detail pane's Add link, and the
// Tracker's Log it.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/url-less";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

const JD =
  "We are hiring a Staff Platform Engineer to own our ingestion pipeline. " +
  "You will design services in Python, run them on AWS, and mentor two " +
  "junior engineers. Five years of backend experience is required, and " +
  "you will be the first platform hire on a team of nine.";

// Its own text, because the 2 tests share one sidecar and a URL-less job is
// keyed on its description: pasting this one's JD into the other's would
// correctly resolve to that job instead of writing a second row.
const TRACKER_JD =
  "Umbrella is looking for a Logged Engineer to run our billing platform. " +
  "You will own Go services on GCP, carry the on-call pager one week in six, " +
  "and work with a product manager on quarterly roadmaps. Four years of " +
  "experience with distributed systems is what we are looking for.";

test("a job with no link: add it, see it, filter it, give it a link", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer, Python and AWS." },
  });
  // Keyword scoring: the mode a user with no provider key runs in, and the
  // one that makes triage free. It also gives the strip a method to name,
  // which is what section 8.7 owes the user.
  const settings = await (await request.get(`${base}/api/settings`, { headers: auth })).json();
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: {
      thresholds: { ...(settings.preferences?.thresholds ?? {}), scoring_mode: "keyword" },
    },
  });
  // A scanned job to sit beside it, so the Link filter has both populations.
  await request.post(`${base}/api/jobs`, {
    headers: auth,
    data: {
      canonical_url: "https://example.com/e2e-scanned-job",
      title: "Scanned Engineer",
      company: "Globex",
      location: "Remote",
      description: "A job that arrived with a link on it.",
      source_adapter: "greenhouse",
    },
  });

  await page.goto("/jobs");

  // ── Drawing 1: the modal opens as 1 form, every field visible, no gate ──
  await page.getByTestId("add-job").click();
  const modal = page.getByTestId("add-job-url-input");
  await expect(modal).toBeVisible();
  await expect(page.getByTestId("add-job-description")).toBeVisible();
  await expect(page.getByTestId("add-job-salary")).toBeVisible();
  await page.screenshot({ path: `${DIR}/01-add-a-job-opened.png`, fullPage: true });

  // Title and description are the whole gate — the link is not required.
  await expect(page.getByTestId("add-job-submit-btn")).toBeDisabled();
  await page.getByTestId("add-job-title").fill("Staff Platform Engineer");
  await page.getByTestId("add-job-company").fill("Initech");
  await page.getByTestId("add-job-description").fill(JD);
  await expect(page.getByTestId("add-job-submit-btn")).toBeEnabled();
  await page.screenshot({ path: `${DIR}/02-filled-no-link.png`, fullPage: true });

  // ── Drawing 2: triage scores it with nothing saved ──
  await page.getByTestId("add-job-score-first").click();
  await expect(page.getByTestId("triage-strip")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("triage-strip")).toContainText("Scored with: keyword");
  await page.screenshot({ path: `${DIR}/03-triage.png`, fullPage: true });

  await page.getByTestId("add-job-submit-btn").click();

  // ── Drawings 4 and 5: the card says it has no link, and why that matters ──
  await expect(page.getByText("Staff Platform Engineer").first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("no-link-chip").first()).toBeVisible();
  await expect(page.getByTestId("trust-chip").first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/04-card-chips.png`, fullPage: true });

  // ── Drawing 4: the Link filter group splits the board on the URL axis ──
  await page.locator("#filter-link").getByText("No link").click();
  await expect(page.getByText("Staff Platform Engineer").first()).toBeVisible();
  await expect(page.getByText("Scanned Engineer")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/05-filter-no-link.png`, fullPage: true });

  await page.locator("#filter-link").getByText("Has link").click();
  await expect(page.getByText("Scanned Engineer").first()).toBeVisible();
  await expect(page.getByText("Staff Platform Engineer")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/06-filter-has-link.png`, fullPage: true });

  // ── Drawing 6: "Open posting" is "Add link" when there's nothing to open ──
  await page.locator("#filter-link").getByText("No link").click();
  await page.getByText("Staff Platform Engineer").first().click();
  await expect(page.getByTestId("add-link-open")).toBeVisible();
  await page.screenshot({ path: `${DIR}/07-detail-add-link.png`, fullPage: true });

  // ── Drawing 7: the promotion dialog, and its unreadable-page branch ──
  await page.getByTestId("add-link-open").click();
  await expect(page.getByTestId("add-link-dialog")).toBeVisible();
  await page.screenshot({ path: `${DIR}/08-add-link-dialog.png`, fullPage: true });

  await page.getByTestId("add-link-input").fill("https://127.0.0.1:9/no-such-posting");
  await page.getByTestId("add-link-attach").click();
  // Nothing to compare against, so it says so and offers to take our word.
  await expect(page.getByTestId("add-link-unreadable")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: `${DIR}/09-add-link-unreadable.png`, fullPage: true });

  await page.getByTestId("add-link-confirm").click();
  await expect(page.getByTestId("add-link-dialog")).toHaveCount(0, { timeout: 15_000 });

  // Promoted: it crossed to the Has-link side. Crossing the filter first is
  // load-bearing (S-C71): the pane falls back to `visible[0]`, so asserting while
  // No-link is still on reads a URL-less neighbour's Add link button. On the
  // Has-link side no job can offer one, and the click pins the pane.
  await page.locator("#filter-link").getByText("Has link").click();
  await expect(page.getByText("Staff Platform Engineer").first()).toBeVisible();
  await page.getByText("Staff Platform Engineer").first().click();
  await expect(page.getByText("Open posting").first()).toBeVisible();
  await expect(page.getByTestId("add-link-open")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/10-promoted.png`, fullPage: true });
});

test("the tracker logs an application with no link", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });

  await page.goto("/applications");
  await page.getByTestId("add-application-btn").click();

  // Drawing 3: same fields, same rules, plus stage, documents and notes.
  await expect(page.getByTestId("add-job-url-input")).toBeVisible();
  await expect(page.getByTestId("add-app-stage")).toBeVisible();
  await expect(page.getByTestId("add-app-submit-btn")).toBeDisabled();

  await page.getByTestId("add-job-title").fill("Logged Engineer");
  await page.getByTestId("add-job-company").fill("Umbrella");
  await page.getByTestId("add-job-description").fill(TRACKER_JD);
  await page.screenshot({ path: `${DIR}/11-log-application.png`, fullPage: true });

  await page.getByTestId("add-app-submit-btn").click();
  await expect(page.getByText("Logged Engineer").first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${DIR}/12-logged.png`, fullPage: true });
});

// Its own description, for the same keying reason as TRACKER_JD above.
const TOMBSTONE_JD =
  "Initech needs a Ledger Engineer to maintain our settlement service. " +
  "You will write Rust, operate Postgres replicas, and own the nightly " +
  "reconciliation run. Six years of systems experience is the bar, and " +
  "the team is four engineers reporting to a staff lead.";

test("delete forever has room for its confirm, and a typed-in job can be pasted back", async ({
  page,
}) => {
  await page.goto("/jobs");
  await page.getByTestId("add-job").click();
  await page.getByTestId("add-job-title").fill("Ledger Engineer");
  await page.getByTestId("add-job-company").fill("Initech");
  await page.getByTestId("add-job-description").fill(TOMBSTONE_JD);
  await page.getByTestId("add-job-submit-btn").click();
  await expect(page.getByText("Ledger Engineer").first()).toBeVisible({ timeout: 15_000 });

  // Trash it from the detail pane, then open the roster.
  await page.getByText("Ledger Engineer").first().click();
  await page.getByTestId("remove-job").click();
  await page.getByTestId("trash-btn").click();
  await expect(page.getByTestId("trash-modal")).toBeVisible();

  // The whole-roster button is 1 line of its own now, rather than wedged
  // beside the retention sentence (maintainer, 2026-10-08).
  const emptyAll = page.getByTestId("trash-empty-btn");
  await expect(emptyAll).toBeVisible();
  expect((await emptyAll.boundingBox())?.width ?? 0).toBeGreaterThan(400);
  await page.screenshot({ path: `${DIR}/13-trash-roster.png`, fullPage: true });

  // Arming a row's confirm opens its own line under the row. A typed-in job
  // carries no "can't paste it again" warning: it is never tombstoned
  // (maintainer, 2026-10-10).
  await page.getByTestId("trash-delete-forever-btn").first().click();
  await expect(page.getByTestId("trash-delete-forever-confirm-btn").first()).toBeVisible();
  await expect(page.getByText("paste it again")).toHaveCount(0);
  await page.screenshot({ path: `${DIR}/14-trash-confirm.png`, fullPage: true });

  await page.getByTestId("trash-delete-forever-confirm-btn").first().click();
  await expect(page.getByTestId("trash-delete-forever-btn")).toHaveCount(0, { timeout: 15_000 });
  await page.keyboard.press("Escape");

  // Paste the same description back: it lands on the board again.
  await page.getByTestId("add-job").click();
  await page.getByTestId("add-job-title").fill("Ledger Engineer");
  await page.getByTestId("add-job-description").fill(TOMBSTONE_JD);
  await page.getByTestId("add-job-submit-btn").click();
  await expect(page.getByTestId("add-job-submit-btn")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByTestId("add-job-save-error")).toHaveCount(0);
  await expect(
    page.getByTestId("job-row").filter({ hasText: "Ledger Engineer" }),
  ).toHaveCount(1, { timeout: 15_000 });
  await page.screenshot({ path: `${DIR}/15-pasted-back.png`, fullPage: true });
});
