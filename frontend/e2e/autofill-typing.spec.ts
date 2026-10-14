// Covers: typing in the Add a job posting form while the link is still being read
// (audit A14, `6f8641b`). The fetch is held for 2 s; what the user typed in that
// time stays, and the fetch fills only the fields still empty.

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/autofill";

test("a field typed during a slow autofill keeps what was typed", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { resume_markdown: "# E2E Candidate\n\nBackend engineer." },
  });
  let release: () => void = () => {};
  const held = new Promise<void>((r) => (release = r));
  await page.route("**/api/jobs/preview", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await held;
    await route.fulfill({
      status: 200,
      headers: { "access-control-allow-origin": "*" },
      json: {
        canonical_url: "https://example.com/e2e-slow-autofill",
        title: "Fetched Title",
        company: "FetchCo",
        location: "Berlin, Germany",
        description: "Fetched description. ".repeat(20),
        posted_at: null,
        salary: "$120k",
        source_adapter: "greenhouse",
      },
    });
  });

  await page.goto("/jobs");
  await page.getByTestId("add-job").click();
  await page.getByTestId("add-job-url-input").fill("https://example.com/e2e-slow-autofill");
  await page.getByTestId("add-job-title").click(); // blur the link: the read starts
  await expect(page.getByTestId("autofill-reading")).toBeVisible();

  // Typed while the read is in flight.
  await page.getByTestId("add-job-title").fill("My Own Title");
  await page.getByTestId("add-job-company").fill("Typed Co");
  release();

  await expect(page.getByTestId("autofill-filled")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("add-job-title")).toHaveValue("My Own Title");
  await expect(page.getByTestId("add-job-company")).toHaveValue("Typed Co");
  await expect(page.getByTestId("add-job-location")).toHaveValue("Berlin, Germany");
  await expect(page.getByTestId("add-job-salary")).toHaveValue("$120k");
  await page.screenshot({ path: `${DIR}/typed-during-fetch.png`, fullPage: true });
});
