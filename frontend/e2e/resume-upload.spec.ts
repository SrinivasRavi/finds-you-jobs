// The Upload affordance across the three resume modals (2026-07-28):
//  - Master  → Upload EXTRACTS a document's text into the editor (multi-format);
//              Upload + Preview|Raw are right-aligned.
//  - Tailored/Cover → Upload ATTACHES the actual file as the application's
//              submitted document (a chip beside the editable variant that
//              coexists with any generated markdown); controls right-aligned.
// The extraction formats are unit-tested (test_doc_extract.py) and the attach
// endpoint (test_applications_vertical.py); this pins the live wiring + layout.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type APIRequestContext } from "@playwright/test";

const DIR = "e2e/_screenshots/resume-upload";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

const DOCX_MIME =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test.beforeEach(async ({ request }) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# Tester\n\nBackend engineer." },
  });
});

/** A discovered (editable) tracker card to open the tailored/cover modals on. */
async function createApp(request: APIRequestContext): Promise<void> {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: `https://example.com/e2e-upload-${Date.now()}`,
        title: "Staff Backend Engineer",
        company: "Globex",
        location: "Remote",
        description: "Own the platform.",
        source_adapter: "paste-url",
      },
    })
  ).json();
  await request.post(`${base}/api/applications`, {
    headers: auth,
    data: { job_id: job.id, generate_resume: false, generate_cover: false },
  });
}

test("master: Upload extracts a document into the editor; controls right-aligned", async ({
  page,
}) => {
  await page.goto("/jobs");
  await page.locator("[data-action='open-master-resume']").click();
  await expect(page.getByTestId("master-editor")).toBeVisible({ timeout: 15_000 });
  // Relabeled "Upload" (not "Upload PDF"), sharing the right edge with Preview|Raw.
  await expect(page.getByTestId("upload-doc-master")).toBeVisible();
  await expect(page.getByTestId("upload-doc-master")).toContainText("Upload");
  await page.mouse.move(4, 4);
  await page.screenshot({ path: `${DIR}/master.png`, fullPage: true });

  // Upload a (multi-format) document — extracted text lands in the editor.
  await page.getByTestId("upload-doc-master-input").setInputFiles({
    name: "uploaded.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Uploaded Heading\n\nFrom my own file."),
  });
  await expect(
    page.getByTestId("master-editor").getByRole("heading", { name: "Uploaded Heading" }),
  ).toBeVisible();
});

test("tailored: Upload attaches the actual file as the submitted document", async ({
  page,
  request,
}) => {
  await createApp(request);
  await page.goto("/applications");
  await page.getByTestId("card-resume-slot").first().click();

  await expect(page.getByTestId("tailored-editor")).toBeVisible({ timeout: 15_000 });
  // Order on the right: Generate · Upload · Preview|Raw.
  await expect(page.getByTestId("generate-variant")).toBeVisible();
  await expect(page.getByTestId("upload-doc-variant")).toBeVisible();
  await expect(page.getByTestId("mode-preview")).toBeVisible();
  await page.mouse.move(4, 4);
  await page.screenshot({ path: `${DIR}/tailored.png`, fullPage: true });

  // Attach a real file → a chip appears with its name (what Apply submits).
  await page.getByTestId("upload-doc-variant-input").setInputFiles({
    name: "my-resume.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 fake resume"),
  });
  await expect(page.getByTestId("attached-doc-chip")).toContainText("my-resume.pdf");
  // The editor (generated variant) still there — file and markdown coexist.
  await expect(page.getByTestId("tailored-editor")).toBeVisible();
  await page.screenshot({ path: `${DIR}/tailored-attached.png`, fullPage: true });

  // Detach removes the chip.
  await page.getByTestId("attached-doc-remove").click();
  await expect(page.getByTestId("attached-doc-chip")).toHaveCount(0);
});

test("cover: Upload attaches the actual file; controls right-aligned", async ({
  page,
  request,
}) => {
  await createApp(request);
  await page.goto("/applications");
  await page.getByTestId("card-cover-slot").first().click();

  await expect(page.getByTestId("cover-editor")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("upload-doc-variant")).toBeVisible();
  await page.mouse.move(4, 4);
  await page.screenshot({ path: `${DIR}/cover.png`, fullPage: true });

  await page.getByTestId("upload-doc-variant-input").setInputFiles({
    name: "cover.docx",
    mimeType: DOCX_MIME,
    buffer: Buffer.from("PK\x03\x04 fake docx"),
  });
  await expect(page.getByTestId("attached-doc-chip")).toContainText("cover.docx");
  await page.screenshot({ path: `${DIR}/cover-attached.png`, fullPage: true });
});
