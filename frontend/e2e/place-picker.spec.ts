// Covers: the location picker in Job finder preferences
// (docs/internal/place-picker.md section 3). Typing "London" offers both Londons
// named in full; the user's pick is stored as the place itself and comes back on
// reopen; Enter without picking shows which London was taken; unmatched text is
// kept as typed. Round-trips through the live sidecar.

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/place-picker";

test("the picker asks which London, and stores the one picked", async ({ page, request }) => {
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
      locations: ["Bengaluru"],
      portals_config: {
        sources: [{ url: "https://boards.greenhouse.io/e2e-nonexistent-tenant" }],
      },
    },
  });

  await page.goto("/jobs");
  await page.getByTestId("finder-prefs").click();
  const input = page.getByTestId("fp-locations-input");
  const box = page.getByTestId("fp-locations");

  // A saved plain-string chip from before the picker still shows.
  await expect(box.getByText("Bengaluru")).toBeVisible();

  // Both Londons, each named in full.
  await input.fill("London");
  const options = page.getByTestId("fp-locations-option");
  await expect(options.first()).toHaveText("London, England, United Kingdom");
  await expect(options.filter({ hasText: "London, Ontario, Canada" })).toHaveCount(1);
  await page.screenshot({ path: `${DIR}/01-both-londons.png`, fullPage: true });

  // Pick Ontario with the keyboard.
  const ontario = await options.allTextContents();
  const down = ontario.indexOf("London, Ontario, Canada");
  for (let i = 0; i < down; i += 1) await input.press("ArrowDown");
  await input.press("Enter");
  await expect(box.getByText("London, Ontario, Canada")).toBeVisible();

  // Enter without picking takes the top one, and says which in full.
  await input.fill("London");
  await expect(options.first()).toHaveText("London, England, United Kingdom");
  await input.press("Enter");
  await expect(box.getByText("London, England, United Kingdom")).toBeVisible();

  // Text that matches no place is kept as typed.
  await input.fill("Moonbase Alpha");
  await expect(page.getByTestId("fp-locations-suggestions")).toContainText(
    "No place matched",
  );
  await input.press("Enter");
  await expect(box.getByText("Moonbase Alpha")).toBeVisible();
  await page.screenshot({ path: `${DIR}/02-chips.png`, fullPage: true });

  // Save, reopen: the chips came back from the store.
  await page.getByTestId("finder-prefs-save").click();
  await expect(page.getByTestId("fp-locations")).toHaveCount(0, { timeout: 15_000 });
  await page.getByTestId("finder-prefs").click();
  for (const label of [
    "Bengaluru",
    "London, Ontario, Canada",
    "London, England, United Kingdom",
    "Moonbase Alpha",
  ]) {
    await expect(page.getByTestId("fp-locations").getByText(label)).toBeVisible();
  }
  await page.screenshot({ path: `${DIR}/03-reopened.png`, fullPage: true });

  // The store holds the places themselves, so the scorer never guesses.
  const settings = await (await request.get(`${base}/api/settings`, { headers: auth })).json();
  expect(settings.preferences.locations).toEqual([
    "Bengaluru",
    { label: "London, Ontario, Canada", name: "London", level: "city", id: "6058560" },
    { label: "London, England, United Kingdom", name: "London", level: "city", id: "2643743" },
    "Moonbase Alpha",
  ]);
});

test("the board prints a posting's place the way the picker names it", async ({
  page,
  request,
}) => {
  // place-picker.md 8.3: 2 spellings of Bengaluru print 1 label, with the work
  // style after it; the job pane keeps the text as posted.
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  const jd =
    "Placeco builds payment rails. You will run Python services and PostgreSQL from our " +
    "Bengaluru office three days a week in a hybrid setup, with the platform team.";
  const ids: string[] = [];
  for (const [n, location] of [[1, "Bangalore - Engineering"], [2, "India - Bangalore"]] as const) {
    const r = await request.post(`${base}/api/jobs`, {
      headers: auth,
      data: {
        canonical_url: `https://example.com/placeco-${n}`,
        title: `Placeco Engineer ${n}`,
        company: "Placeco",
        location,
        description: jd,
        source_adapter: "greenhouse",
      },
    });
    ids.push((await r.json()).id as string);
  }

  await page.goto("/jobs");
  await page.getByTestId("board-text-search").fill("placeco");
  const rows = page.getByTestId("job-row");
  await expect(rows).toHaveCount(2, { timeout: 15_000 });
  for (const row of await rows.all()) {
    await expect(row).toContainText("Placeco · Bengaluru, Karnataka, India (Hybrid)");
  }
  await rows.filter({ hasText: "Placeco Engineer 1" }).click();
  await expect(page.getByTestId("job-location-as-posted")).toHaveText(
    "As posted: Bangalore - Engineering",
  );
  await page.screenshot({ path: `${DIR}/04-board-resolved-places.png`, fullPage: true });

  for (const id of ids) {
    await request.post(`${base}/api/jobs/${id}/tombstone`, { headers: auth });
  }
});

test("a posting that only says Remote prints it once", async ({ page, request }) => {
  // The text names no place and repeats the work-style label, which was
  // "Remote · Remote" on the row before 4e7265b.
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  const r = await request.post(`${base}/api/jobs`, {
    headers: auth,
    data: {
      canonical_url: "https://example.com/remoteco-1",
      title: "Remoteco Engineer",
      company: "Remoteco",
      location: "Remote",
      description:
        "Remoteco builds billing software. This is a fully remote role: you will run Python " +
        "services and PostgreSQL from home, with the platform team.",
      source_adapter: "greenhouse",
    },
  });
  const id = (await r.json()).id as string;

  await page.goto("/jobs");
  await page.getByTestId("board-text-search").fill("remoteco");
  const row = page.getByTestId("job-row");
  await expect(row).toHaveCount(1, { timeout: 15_000 });
  await expect(row).toContainText("Remoteco · Remote");
  await expect(row).not.toContainText("Remote · Remote");
  await page.screenshot({ path: `${DIR}/05-board-remote-once.png`, fullPage: true });

  await request.post(`${base}/api/jobs/${id}/tombstone`, { headers: auth });
});
