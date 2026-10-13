// Covers: the Applier UI against the live sidecar. The card's Apply slot follows
// the run, the companion panel binds to a run (section 9.2) and shows the handoff
// strip at ready_for_human, and "I submitted" moves the card to Applied.
// Zero model calls and zero external traffic: FYJ_APPLY_DEV's scripted engine
// runs against a local file:// fixture form in the sidecar's own Chromium.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/applier";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

const FIXTURE_FORM = pathToFileURL(
  join(SPEC_DIR, "..", "..", "sidecar", "tests", "packages", "jobapplier", "fixtures", "form.html"),
).href;

test("apply run: card slot, companion panel, attest to Applied", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  // Apply refuses without the facts read from the resume (audit A5).
  await request.patch(`${base}/api/profile/application-profile`, {
    headers: auth,
    data: { name: "E2E Candidate", email: "e2e@example.com" },
  });
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        // No link: a `file://` fixture is not a posting URL, and
        // `canonicalize_url` rejects it, so the job is stored URL-less and the
        // form travels in the dev knobs below where every other dev-only input
        // already lives.
        canonical_url: "",
        title: "Platform Engineer",
        company: "Acme",
        location: "Remote",
        description: "Keep the lights on.",
        source_adapter: "paste-url",
      },
    })
  ).json();
  const application = await (
    await request.post(`${base}/api/applications`, {
      headers: auth,
      data: { job_id: job.id, generate_resume: false, generate_cover: false },
    })
  ).json();

  // Start the run via the API with the dev scripted engine (section test seam) —
  // the UI's own Apply button uses the same route without dev knobs.
  const run = await (
    await request.post(`${base}/api/applications/${application.id}/apply`, {
      headers: auth,
      data: {
        dev: {
          form_url: FIXTURE_FORM,
          engine_script: [
            JSON.stringify({ tool: "fill", element_id: "e2", value: "Ada Lovelace" }),
            JSON.stringify({ tool: "finish", reason: "grounded fields filled; review the rest" }),
          ],
          allow_local: true,
          headed: false,
          review_wait_s: 0,
        },
      },
    })
  ).json();

  // Wait for the terminal state server-side, then verify the UI reads it.
  await expect(async () => {
    const snap = await (
      await request.get(`${base}/api/apply-runs/${run.id}`, { headers: auth })
    ).json();
    expect(["ready_for_human"]).toContain(snap.status);
  }).toPass({ timeout: 60_000 });

  await page.goto("/applications");
  await expect(page.getByText("Platform Engineer").first()).toBeVisible({
    timeout: 15_000,
  });
  // The card's Apply slot reflects the run: ready for review.
  const slot = page.getByTestId("card-apply-slot").first();
  await expect(slot).toBeVisible();
  await page.screenshot({ path: `${DIR}/card-ready-for-review.png`, fullPage: true });

  // Open the companion — it binds to the existing run via snapshot (section 9.2).
  await slot.click();
  const panel = page.getByTestId("applier-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("applier-handoff-strip")).toBeVisible();
  await expect(page.getByTestId("applier-cost-line")).toBeVisible();
  await page.screenshot({ path: `${DIR}/panel-ready-for-human.png`, fullPage: true });

  // S-A5: "Submit it for me" is offered, and it is two-step. P1 puts a
  // confirmation in front of every irreversible action, so the first click
  // only reveals the confirm; nothing is sent until the second.
  const submitBtn = page.getByTestId("applier-submit-btn");
  await expect(submitBtn).toBeVisible();
  await expect(page.getByTestId("applier-submit-confirm")).toHaveCount(0);
  await submitBtn.click();
  const confirm = page.getByTestId("applier-submit-confirm");
  await expect(confirm).toBeVisible();
  await expect(confirm).toContainText("cannot be undone");
  await page.screenshot({ path: `${DIR}/submit-confirm.png`, fullPage: true });

  // Backing out sends nothing and leaves the run exactly where it was.
  await page.getByTestId("applier-submit-cancel-btn").click();
  await expect(page.getByTestId("applier-submit-confirm")).toHaveCount(0);
  const stillReady = await (
    await request.get(`${base}/api/apply-runs/${run.id}`, { headers: auth })
  ).json();
  expect(stillReady.status).toBe("ready_for_human");

  // Attest: "I submitted" → the card advances to Applied (section 8.4).
  await page.getByTestId("applier-attest-submitted-btn").click();
  await expect(async () => {
    const card = await (
      await request.get(`${base}/api/applications/${application.id}`, { headers: auth })
    ).json();
    expect(card.column).toBe("applied");
  }).toPass({ timeout: 10_000 });
  await page.screenshot({ path: `${DIR}/attested-submitted.png`, fullPage: true });
});

test("Apply with no facts read from the resume says why and offers to read them", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "https://example.com/e2e-no-facts-job",
        title: "No Facts Engineer",
        company: "Initech",
        location: "Remote",
        description: "Own the monolith.",
        source_adapter: "paste-url",
      },
    })
  ).json();
  await request.post(`${base}/api/applications`, {
    headers: auth,
    data: { job_id: job.id, generate_resume: false, generate_cover: false },
  });
  // The API can't clear facts once set, so the profile read is served with none
  // (audit A5). Apply must not be called; reading the facts must be.
  await page.route("**/api/profile", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        resume_markdown: "# E2E Candidate\n\nBackend engineer.",
        version: 1,
        application_profile: null,
      }),
    }),
  );
  const applyCalls: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/apply$/.test(r.url())) applyCalls.push(r.url());
  });
  let extractCalls = 0;
  await page.route("**/api/profile/extract", (route) => {
    extractCalls += 1;
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ id: "op-e2e", kind: "extract", state: "queued" }),
    });
  });
  await page.goto("/applications");
  const slot = page
    .getByTestId("tracker-card")
    .filter({ hasText: "No Facts Engineer" })
    .getByTestId("card-apply-slot");
  await expect(slot).toBeVisible({ timeout: 15_000 });
  await slot.click();
  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toContainText("haven't been read yet");
  await page.screenshot({ path: `${DIR}/apply-no-facts-dialog.png`, fullPage: true });
  await page.getByTestId("confirm-ok").click();
  await expect(dialog).toBeHidden();
  await expect.poll(() => extractCalls).toBe(1);
  expect(applyCalls).toEqual([]);
});
