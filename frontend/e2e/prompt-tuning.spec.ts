// Covers: trying a prompt before committing it (prompt-tuning.md, S-C70).
//
// The shape under test is the maintainer's (2026-10-06): the draft and its test
// runs live inside a popup, 1 button in that popup commits, and the shipped
// prompt stays reachable with Reset outside it.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/prompt-tuning";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test("a prompt draft runs inside the popup, and only its own button commits it", async ({
  page,
  request,
}) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers: auth,
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer, Python and SQL." },
  });
  await request.post(`${base}/api/jobs`, {
    headers: auth,
    data: {
      canonical_url: "https://example.com/e2e-prompt-tuning",
      title: "Backend Engineer",
      company: "Acme",
      location: "Remote",
      description:
        "Backend engineer role. You will build and operate Python services, design " +
        "PostgreSQL schemas, run workloads on AWS, and review other engineers' work. " +
        "Requirements: strong Python, solid SQL, and production ownership experience.",
      source_adapter: "greenhouse",
    },
  });

  await page.goto("/settings");
  await expect(page.getByTestId("settings-nav")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("settings-nav-prompts").click();

  // The pane itself is read-only now: the way to change a prompt is the popup.
  const inline = page.locator('[data-testid="prompt-textarea-score"]');
  await expect(inline).toBeVisible({ timeout: 15_000 });
  await expect(inline).toHaveAttribute("readonly", "");
  // Reset lives out here, where it is always 1 click away.
  await expect(page.locator('[data-testid="prompt-reset-score"]')).toBeVisible();
  await page.screenshot({ path: `${DIR}/pane-readonly.png`, fullPage: true });

  await page.locator('[data-testid="prompt-edit-score"]').click();
  const draft = page.locator('[data-testid="prompt-tune-draft"]');
  await expect(draft).toBeVisible();

  // Nothing to run against yet, so Run is inert rather than failing on click.
  await expect(page.locator('[data-testid="prompt-tune-run"]')).toBeDisabled();
  // And nothing to commit, because the draft is still the saved text.
  await expect(page.locator('[data-testid="prompt-tune-commit"]')).toBeDisabled();

  await page.locator('[data-testid="prompt-test-job"]').selectOption({ index: 1 });
  await expect(page.locator('[data-testid="prompt-tune-run"]')).toBeEnabled();
  // Section 6: the modal says what the run will use before it is pressed.
  await expect(page.locator('[data-testid="prompt-tune-engine"]')).toContainText("Runs on");
  await page.screenshot({ path: `${DIR}/modal-board-source.png`, fullPage: true });

  // Editing the draft wakes the commit button up, and the pane behind still
  // shows the SAVED text, which is the whole point of the window.
  await draft.fill("# A different rubric\n\nScore every job 50.");
  await expect(page.locator('[data-testid="prompt-tune-commit"]')).toBeEnabled();

  // The other input source, for a board with nothing on it.
  await page.locator('[data-testid="prompt-test-source-paste"]').click();
  await expect(page.locator('[data-testid="prompt-test-paste"]')).toBeVisible();
  await expect(page.locator('[data-testid="prompt-tune-run"]')).toBeDisabled();
  await page.locator('[data-testid="prompt-test-paste"]').fill("A pasted job description.");
  await expect(page.locator('[data-testid="prompt-tune-run"]')).toBeEnabled();
  await page.screenshot({ path: `${DIR}/modal-paste-source.png`, fullPage: true });

  // Closing without committing leaves the saved prompt exactly as it was.
  await page.locator('[data-testid="prompt-tune-cancel"]').click();
  await expect(page.locator('[data-testid="prompt-tune-draft"]')).toHaveCount(0);
  const after = await request.get(`${base}/api/settings/prompts`, { headers: auth });
  const rows = (await after.json()) as { kind: string; override_md: string | null }[];
  expect(rows.find((r) => r.kind === "score")?.override_md).toBeNull();
});

test("the kinds that need no job, and the one that needs a contact", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("settings-nav")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("settings-nav-prompts").click();

  // Application profile extraction reads the master resume alone, so it has no
  // picker at all and Run is live immediately.
  await page.locator('[data-testid="prompt-row-extract"]').click();
  await page.locator('[data-testid="prompt-edit-extract"]').click();
  await expect(page.locator('[data-testid="prompt-test-job"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="prompt-test-contact"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="prompt-tune-run"]')).toBeEnabled();
  await page.screenshot({ path: `${DIR}/modal-extract.png`, fullPage: true });
  await page.locator('[data-testid="prompt-tune-cancel"]').click();

  // Referral drafting needs a REAL contact: a made-up one would test the
  // prompt against input that never occurs.
  await page.locator('[data-testid="prompt-row-networker_draft"]').click();
  await page.locator('[data-testid="prompt-edit-networker_draft"]').click();
  await expect(page.locator('[data-testid="prompt-test-contact"]')).toBeVisible();
  await expect(page.locator('[data-testid="prompt-tune-run"]')).toBeDisabled();
  await page.screenshot({ path: `${DIR}/modal-contact.png`, fullPage: true });
});
