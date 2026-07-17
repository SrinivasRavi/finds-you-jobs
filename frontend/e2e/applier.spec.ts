// Covers: the Applier UI against the live sidecar (roadmap commit-15 gate) —
// the tracker card's Apply slot reflects the run lifecycle, the companion
// panel binds to a run (snapshot path, §9.2), shows the honest handoff strip
// at ready_for_human, and "I submitted" attests + moves the card to Applied.
//
// ZERO model calls / ZERO external traffic: the run is started via the API
// with the FYJ_APPLY_DEV scripted-engine knobs against a local file://
// fixture form (the same seam the sidecar integration tests use). The UI
// under test is real; the browser work happens in the sidecar's own
// headless Chromium.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/applier";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

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
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: FIXTURE_FORM,
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

  // Start the run via the API with the dev scripted engine (§ test seam) —
  // the UI's own Apply button uses the same route without dev knobs.
  const run = await (
    await request.post(`${base}/api/applications/${application.id}/apply`, {
      headers: auth,
      data: {
        dev: {
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

  // Open the companion — it binds to the existing run via snapshot (§9.2).
  await slot.click();
  const panel = page.getByTestId("applier-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("applier-handoff-strip")).toBeVisible();
  await expect(page.getByTestId("applier-cost-line")).toBeVisible();
  await page.screenshot({ path: `${DIR}/panel-ready-for-human.png`, fullPage: true });

  // Attest: "I submitted" → the card advances to Applied (§8.4).
  await page.getByTestId("applier-attest-submitted-btn").click();
  await expect(async () => {
    const card = await (
      await request.get(`${base}/api/applications/${application.id}`, { headers: auth })
    ).json();
    expect(card.column).toBe("applied");
  }).toPass({ timeout: 10_000 });
  await page.screenshot({ path: `${DIR}/attested-submitted.png`, fullPage: true });
});
