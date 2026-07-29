// The Master Resume header button is a SHARED launcher placed in the same slot
// — immediately left of the Deleted+Add cluster — on the Job Board,
// Applications, and Networking tabs. Switching tabs must leave it on the exact
// same pixel (maintainer 2026-07-28). This measures its bounding box on each
// tab and asserts they coincide, and captures a header screenshot per tab.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const DIR = "e2e/_screenshots/master-resume-alignment";
const SPEC_DIR = dirname(fileURLToPath(import.meta.url));

function sidecarInfo(): { base: string; token: string } {
  const env = readFileSync(join(SPEC_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { base: `http://127.0.0.1:${port}`, token };
}

test.beforeEach(async ({ request }) => {
  // A saved profile lands us on the real tabs (not onboarding).
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# Tester\n\nSome experience." },
  });
});

async function box(page: Page, selector: string) {
  const el = page.locator(selector);
  await expect(el).toBeVisible({ timeout: 15_000 });
  const b = await el.boundingBox();
  if (!b) throw new Error(`no bounding box for ${selector}`);
  return b;
}

/** Grab the header action buttons that must line up across tabs: the shared
 *  Master Resume launcher and the per-tab "Deleted …" button (its testid
 *  differs per tab, but the box must coincide). */
async function headerBoxes(page: Page, path: string, deletedTestid: string) {
  await page.goto(path);
  return {
    master: await box(page, "[data-action='open-master-resume']"),
    deleted: await box(page, `[data-testid='${deletedTestid}']`),
  };
}

function sameBox(a: { x: number; y: number; width: number; height: number }, b: typeof a) {
  // Pixel-level: x, right edge, y, width and height coincide (sub-pixel
  // tolerance absorbs float rounding only).
  expect(Math.abs(b.x - a.x)).toBeLessThan(0.5);
  expect(Math.abs(b.x + b.width - (a.x + a.width))).toBeLessThan(0.5);
  expect(Math.abs(b.y - a.y)).toBeLessThan(0.5);
  expect(Math.abs(b.width - a.width)).toBeLessThan(0.5);
  expect(Math.abs(b.height - a.height)).toBeLessThan(0.5);
}

test("Master Resume + Deleted buttons land on the same pixel across all three tabs", async ({
  page,
}) => {
  const jobs = await headerBoxes(page, "/jobs", "trash-btn");
  await page.screenshot({ path: `${DIR}/jobs.png` });

  const applications = await headerBoxes(page, "/applications", "archive-btn");
  await page.screenshot({ path: `${DIR}/applications.png` });

  const networking = await headerBoxes(page, "/networking", "deleted-contacts-btn");
  await page.screenshot({ path: `${DIR}/networking.png` });

  for (const other of [applications, networking]) {
    sameBox(jobs.master, other.master);
    sameBox(jobs.deleted, other.deleted);
  }

  // And it actually opens the master editor from a non-Job-Board tab (the
  // shared launcher owns the modal, not the Job Board) — currently on
  // /networking from the loop above.
  await page.locator("[data-action='open-master-resume']").click();
  await expect(page.getByTestId("master-editor")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("md-toolbar")).toBeVisible();
  await page.screenshot({ path: `${DIR}/networking-master-open.png` });
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("master-editor")).toHaveCount(0);
});

test("a nonzero Deleted count neither shifts nor overflows the button box", async ({
  page,
  request,
}) => {
  // The count suffix is absolutely positioned so it can't change the button's
  // width — that's what keeps the box aligned across tabs even when only one
  // tab has deleted items. Prove it on the LONGEST-label tab (Applications),
  // where a count has the least slack.
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };

  await page.goto("/applications");
  const empty = await box(page, "[data-testid='archive-btn']");

  const job = await (
    await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: "https://example.com/e2e-deleted-count",
        title: "QA Engineer",
        company: "Acme",
        location: "Remote",
        description: "x",
        source_adapter: "paste-url",
      },
    })
  ).json();
  const app = await (
    await request.post(`${base}/api/applications`, {
      headers: auth,
      data: { job_id: job.id, generate_resume: false, generate_cover: false },
    })
  ).json();
  await request.patch(`${base}/api/applications/${app.id}`, {
    headers: auth,
    data: { archived: true },
  });

  await page.reload();
  await expect(page.getByTestId("archive-btn")).toContainText("(1)");
  const withCount = await box(page, "[data-testid='archive-btn']");
  await page.screenshot({ path: `${DIR}/applications-with-count.png` });

  // Same box — the count rode along without widening or moving the button.
  sameBox(empty, withCount);
});
