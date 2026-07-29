// Covers the reworked resume editor: Preview is ONE directly-editable rendered
// surface (cursor in the formatted text, toolbar applies real formatting), Raw
// is the same pane as markdown source, Upload PDF sits in the master header,
// and the tailored variant is always an editable box (paste-your-own) with
// Generate at the top. The md↔DOM fidelity contract itself is pinned by the
// mdHtml.test.ts unit suite; the backend create-if-missing save is pinned by
// test_patch_artifact_creates_manual_variant_when_missing.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/resume-editor";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

const RESUME_MD = `# Tenet Loader

**Headline:** Forward-deployed engineer building distributed backends.

**Email:** [tenetloader@gmail.com](mailto:tenetloader@gmail.com)

## Experience

- Owned the billing platform: event-driven pipelines in Python and Go.
- Postgres at scale, Kubernetes across three regions.

## Highlights

1. Cut p99 latency by 40%.
2. Led a team of five engineers.`;

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
    data: { resume_markdown: RESUME_MD },
  });
});

test("master resume: Preview is one directly-editable rendered surface; Raw is the source", async ({
  page,
}) => {
  await page.goto("/jobs");
  await page.locator("[data-action='open-master-resume']").click();

  const editor = page.getByTestId("master-editor");
  // ONE pane: the editable surface IS the rendered view — no split, no
  // secondary preview pane.
  await expect(editor).toBeVisible({ timeout: 15_000 });
  await expect(editor).toHaveAttribute("contenteditable", "true");
  await expect(page.getByTestId("master-editor-preview")).toHaveCount(0);
  await expect(page.getByTestId("md-toolbar")).toBeVisible();
  // The surface shows the FORMATTED resume (rendered heading + real link).
  await expect(editor.getByRole("heading", { name: "Tenet Loader" })).toBeVisible();
  await expect(editor.getByRole("link", { name: "tenetloader@gmail.com" })).toBeVisible();
  // Upload PDF + the two visible modes.
  await expect(page.getByTestId("upload-doc-master")).toBeVisible();
  await expect(page.getByTestId("mode-preview")).toBeVisible();
  await expect(page.getByTestId("mode-raw")).toBeVisible();
  await page.mouse.move(4, 4);
  await page.screenshot({ path: `${DIR}/master-preview-editable.png`, fullPage: true });

  // Direct editing: click INTO the rendered text and type — the cursor is in
  // the formatted view and the edit registers (dirty state appears).
  await editor.getByRole("heading", { name: "Tenet Loader" }).click();
  await page.keyboard.press("End");
  await page.keyboard.type(" III");
  await expect(editor.getByRole("heading", { name: "Tenet Loader III" })).toBeVisible();
  await expect(page.getByText("Unsaved changes")).toBeVisible();

  // Toolbar applies real formatting to the surface, and it serializes back to
  // markdown: select the word "Postgres", click Bold, then Raw must show the
  // ** markers around exactly that word. (Selected via Range: Playwright's
  // synthesized dblclick doesn't word-select inside contenteditable — a
  // harness quirk, verified against a probe; a real double-click does.)
  const selectPostgres = () =>
    editor.evaluate((el) => {
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let node: Node | null;
      while ((node = walker.nextNode())) {
        const i = node.textContent?.indexOf("Postgres") ?? -1;
        if (i >= 0) {
          const range = document.createRange();
          range.setStart(node, i);
          range.setEnd(node, i + "Postgres".length);
          const sel = window.getSelection();
          sel?.removeAllRanges();
          sel?.addRange(range);
          return;
        }
      }
      throw new Error("word not found");
    });
  await selectPostgres();
  await page.getByTestId("md-tool-bold").click();
  // The wrap is visible immediately in the rendered surface…
  await expect(editor.locator("strong", { hasText: "Postgres" })).toBeVisible();

  // …and Bold TOGGLES: a second click on the now-bold word removes it (never
  // stacks another **…** — the reported bug), a third puts it back, leaving the
  // document bold again for the Raw assertion below.
  await selectPostgres();
  await page.getByTestId("md-tool-bold").click();
  await expect(editor.locator("strong", { hasText: "Postgres" })).toHaveCount(0);
  await selectPostgres();
  await page.getByTestId("md-tool-bold").click();
  await expect(editor.locator("strong", { hasText: "Postgres" })).toBeVisible();

  await page.getByTestId("mode-raw").click();
  await expect(page.getByTestId("md-toolbar")).toHaveCount(0);
  await expect(page.getByTestId("master-editor")).toHaveValue(/\*\*Postgres\*\*/);
  await expect(page.getByTestId("master-editor")).toHaveValue(/Tenet Loader III/);
  await page.screenshot({ path: `${DIR}/master-raw.png`, fullPage: true });

  // And back: Preview must re-render the document — NOT come up blank (the
  // Raw → Preview blank-surface regression).
  await page.getByTestId("mode-preview").click();
  await expect(page.getByTestId("md-toolbar")).toBeVisible();
  const back = page.getByTestId("master-editor");
  await expect(back.getByRole("heading", { name: "Tenet Loader III" })).toBeVisible();
  await expect(back.locator("strong", { hasText: "Postgres" })).toBeVisible();
  // Round-trip it once more (Preview → Raw → Preview) for good measure.
  await page.getByTestId("mode-raw").click();
  await page.getByTestId("mode-preview").click();
  await expect(
    page.getByTestId("master-editor").getByRole("heading", { name: "Tenet Loader III" }),
  ).toBeVisible();
});

test("tailored variant: one editable box, Generate at top, paste-your-own renders and saves", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "https://example.com/e2e-tailored-paste",
        title: "Staff Backend Engineer",
        company: "Globex",
        location: "Remote",
        description: "Own the platform.",
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

  await page.goto("/applications");
  await page.getByText("Staff Backend Engineer").first().click();
  await page.getByRole("button", { name: "Generate resume", exact: true }).click();

  // Master reference left; ONE editable tailored surface right; Generate at the
  // top of the header; the old "Not generated yet" dead-end is gone.
  await expect(page.getByTestId("tailored-master-ref")).toBeVisible({ timeout: 15_000 });
  const editor = page.getByTestId("tailored-editor");
  await expect(editor).toBeVisible();
  await expect(editor).toHaveAttribute("contenteditable", "true");
  await expect(page.getByTestId("generate-variant")).toBeVisible();
  await expect(page.getByTestId("packet-none")).toHaveCount(0);
  await page.mouse.move(4, 4);
  await page.screenshot({ path: `${DIR}/tailored-editable-empty.png`, fullPage: true });

  // Paste-your-own (the ChatGPT/Gemini flow): a REAL paste event into the empty
  // surface adopts the markdown and renders it immediately.
  const PASTED = "# My own tailored resume\n\nPasted from my ChatGPT.\n\n- Skill one\n- Skill two";
  await editor.click();
  await editor.evaluate((el, text) => {
    const dt = new DataTransfer();
    dt.setData("text/plain", text);
    el.dispatchEvent(
      new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }),
    );
  }, PASTED);
  await expect(editor.getByRole("heading", { name: "My own tailored resume" })).toBeVisible();
  await expect(editor.getByText("Skill one")).toBeVisible();
  await page.screenshot({ path: `${DIR}/tailored-pasted-rendered.png`, fullPage: true });

  // Save persists it as the variant (backend creates it — nothing was generated).
  await page.getByTestId("save-variant").click();
  await expect
    .poll(async () => {
      const app = await (
        await request.get(`${base}/api/applications/${application.id}`, { headers: auth })
      ).json();
      return app.packetResumeState;
    })
    .toBe("ready");
  const saved = await (
    await request.get(`${base}/api/applications/${application.id}`, { headers: auth })
  ).json();
  const artifact = (saved.artifacts as { kind: string; markdown: string }[]).find(
    (a) => a.kind === "tailored_resume",
  );
  expect(artifact?.markdown).toContain("# My own tailored resume");
  expect(artifact?.markdown).toContain("- Skill one");
});
