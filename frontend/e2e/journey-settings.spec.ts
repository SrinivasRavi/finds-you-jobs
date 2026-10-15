// Covers: every Settings control a user fills or clicks that no other spec
// drives by hand. Each test changes a value through the UI, reloads, and reads
// the value back from the page and from the sidecar.

import { expect, test } from "@playwright/test";
import { auth, seedProfile, sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/journey-settings";

test.beforeEach(async ({ request }) => {
  await seedProfile(request);
});

/** The stored preferences, in the sidecar's wire shape. */
async function prefs(request: import("@playwright/test").APIRequestContext) {
  const { base, token } = sidecarInfo();
  const r = await request.get(`${base}/api/settings`, { headers: auth(token) });
  return (await r.json()) as {
    preferences: { thresholds: Record<string, unknown>; ui_state: Record<string, any> };
    engines: { engine: string; has_key: boolean; default_model: string | null }[];
  };
}

async function openPane(page: import("@playwright/test").Page, cat: string) {
  await page.goto("/settings");
  await page.getByTestId(`settings-nav-${cat}`).click();
}

test("theme: Dark, Light and Follow system apply at once and survive a reload", async ({
  page,
}) => {
  await openPane(page, "appearance");
  const html = page.locator("html");
  await page.getByTestId("theme-mode-dark").click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(page.getByTestId("theme-mode-dark")).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: `${DIR}/theme-dark.png`, fullPage: true });

  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");

  await page.getByTestId("settings-nav-appearance").click();
  await page.getByTestId("theme-mode-light").click();
  await expect(html).toHaveAttribute("data-theme", "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.getByTestId("theme-mode-system").click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(html).toHaveAttribute("data-theme", "light");
});

test("language: German translates the app and stays; Arabic reads right to left", async ({
  page,
}) => {
  await openPane(page, "appearance");
  await page.getByTestId("language-select").selectOption("de");
  await expect(page.getByTestId("settings-nav-appearance")).toContainText("Erscheinungsbild");
  await page.screenshot({ path: `${DIR}/language-de.png`, fullPage: true });

  await page.reload();
  await expect(page.getByTestId("settings-nav-appearance")).toContainText("Erscheinungsbild");

  await page.getByTestId("settings-nav-appearance").click();
  await page.getByTestId("language-select").selectOption("ar");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await page.screenshot({ path: `${DIR}/language-ar.png`, fullPage: true });

  await page.getByTestId("language-select").selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await expect(page.getByTestId("settings-nav-appearance")).toContainText("Appearance");
});

test("lifecycle windows save as typed and as picked", async ({ page, request }) => {
  await openPane(page, "data");
  await page.getByTestId("lifecycle-sent-ghosted").fill("21");
  await page.getByTestId("lifecycle-contact-purge").fill("120");
  await page.getByTestId("lifecycle-expire-listing").selectOption("30");
  await page.getByTestId("lifecycle-trashed-jobs-purge").fill("9");
  await expect
    .poll(async () => {
      const lc = (await prefs(request)).preferences.ui_state.lifecycle ?? {};
      return [
        lc.sent_ghosted_days,
        lc.contact_purge_days,
        lc.expire_listing_days,
        lc.trashed_jobs_purge_days,
      ];
    })
    .toEqual([21, 120, 30, 9]);

  await page.reload();
  await page.getByTestId("settings-nav-data").click();
  await expect(page.getByTestId("lifecycle-sent-ghosted")).toHaveValue("21");
  await expect(page.getByTestId("lifecycle-expire-listing")).toHaveValue("30");
  await page.getByTestId("lifecycle-sent-ghosted-row").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${DIR}/lifecycle.png`, fullPage: true });
});

test("observability: content logging, OTLP endpoint and headers, retention", async ({
  page,
  request,
}) => {
  const read = async () => (await prefs(request)).preferences.ui_state;
  await openPane(page, "data");

  const logging = page.getByTestId("content-logging-toggle");
  const before = (await logging.getAttribute("aria-checked")) === "true";
  await logging.click();
  await expect(logging).toHaveAttribute("aria-checked", String(!before));

  await page.getByTestId("otlp-toggle").click();
  await expect(page.getByTestId("otlp-endpoint-row")).toBeVisible();
  await page.getByTestId("otlp-endpoint-row").locator("input").fill("http://127.0.0.1:9/v1/traces");
  // Typed key by key, the way a person types it: "x" alone is no header yet.
  await page.getByTestId("otlp-headers-row").locator("input").pressSequentially("x-team=e2e");
  await expect(page.getByTestId("otlp-headers-row").locator("input")).toHaveValue("x-team=e2e");
  await page.getByTestId("retention-days-row").locator("input").fill("5");
  await expect
    .poll(async () => {
      const o = await read();
      return [o.content_logging, o.otlp_enabled, o.otlp_endpoint, o.otlp_headers, o.retention_days];
    })
    .toEqual([!before, true, "http://127.0.0.1:9/v1/traces", { "x-team": "e2e" }, 5]);
  await page.screenshot({ path: `${DIR}/observability.png`, fullPage: true });

  // Put it back: an OTLP exporter left on would aim every later spec's spans at port 9.
  await page.getByTestId("otlp-toggle").click();
  await expect(page.getByTestId("otlp-endpoint-row")).toHaveCount(0);
  await logging.click();
  await expect.poll(async () => (await read()).otlp_enabled).toBe(false);
});

test("an API key saves sealed, shows as Configured, and Remove forgets it", async ({
  page,
  request,
}) => {
  await openPane(page, "providers");
  await page.getByTestId("provider-tile-anthropic").click();
  await page.getByTestId("engine-key-input").fill("sk-ant-e2e-0000000000001234");
  await page.getByTestId("engine-model-input").fill("claude-e2e-model");
  await page.getByTestId("engine-save-btn").click();

  await expect(page.getByTestId("provider-tile-anthropic")).toContainText("Configured");
  await expect(page.getByTestId("engine-key-input")).toHaveValue("");
  await expect(page.getByTestId("engine-key-input")).toHaveAttribute("placeholder", /Key saved/);
  const settings = await prefs(request);
  const row = settings.engines.find((e) => e.engine === "anthropic");
  expect(row?.has_key).toBe(true);
  expect(row?.default_model).toBe("claude-e2e-model");
  // The key itself never comes back over the API, only a hint.
  expect(JSON.stringify(settings)).not.toContain("sk-ant-e2e-0000000000001234");
  await page.screenshot({ path: `${DIR}/provider-key-saved.png`, fullPage: true });

  await page.getByTestId("engine-delete-btn").click();
  await expect(page.getByTestId("provider-tile-anthropic")).toContainText("Not set");
  await expect(page.getByTestId("engine-delete-btn")).toHaveCount(0);
});

test("applicant details fill, save, and read back after a reload", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  await openPane(page, "applying");
  const details = page.getByTestId("applicant-details");
  await expect(details).toBeVisible();
  await page.getByTestId("applicant-first_name").fill("Ada");
  await page.getByTestId("applicant-last_name").fill("Lovelace");
  await page.getByTestId("applicant-email").fill("ada@example.com");
  await page.getByTestId("applicant-phone").fill("+44 20 7946 0000");
  await page.getByTestId("applicant-location").fill("London, England, United Kingdom");
  await page.getByTestId("applicant-links").fill("github: https://github.com/ada-e2e");
  await page.getByTestId("applicant-notice_period").fill("4 weeks");
  await page.getByTestId("applicant-veteran_status").selectOption("no");
  const rows = page.getByTestId("applicant-auth-row");
  const before = await rows.count();
  await page.getByTestId("applicant-add-auth").click();
  await expect(rows).toHaveCount(before + 1);
  const last = rows.last();
  await last.getByTestId("applicant-auth-country").selectOption("United Kingdom");
  await last.getByTestId("applicant-auth-status").selectOption("citizen");
  await page.getByTestId("applicant-save").click();

  await expect
    .poll(async () => {
      const p = await (await request.get(`${base}/api/profile`, { headers: auth(token) })).json();
      const a = p.application_profile ?? {};
      return [a.first_name, a.email, a.links?.github, a.veteran_status, a.notice_period];
    })
    .toEqual(["Ada", "ada@example.com", "https://github.com/ada-e2e", "no", "4 weeks"]);

  await page.reload();
  await page.getByTestId("settings-nav-applying").click();
  await expect(page.getByTestId("applicant-first_name")).toHaveValue("Ada");
  await expect(page.getByTestId("applicant-location")).toHaveValue("London, England, United Kingdom");
  await expect(page.getByTestId("applicant-auth-country").last()).toHaveValue("United Kingdom");
  await page.screenshot({ path: `${DIR}/applicant-details.png`, fullPage: true });
});

test("the generate-on-save switches for resume and cover letter persist", async ({
  page,
  request,
}) => {
  await openPane(page, "documents");
  const resume = page.getByTestId("auto-resume-toggle");
  const cover = page.getByTestId("auto-cover-toggle");
  const read = async () => {
    const t = (await prefs(request)).preferences.thresholds;
    // No stored value reads as on, as the app reads it (FR-SET-02).
    return [t.auto_resume_on_save ?? true, t.auto_cover_on_save ?? true];
  };
  const [r0, c0] = await read();
  await resume.click();
  await cover.click();
  await expect.poll(read).toEqual([!r0, !c0]);
  await page.reload();
  await page.getByTestId("settings-nav-documents").click();
  await expect(page.getByTestId("auto-resume-toggle")).toHaveAttribute("aria-checked", String(!r0));
  await expect(page.getByTestId("auto-cover-toggle")).toHaveAttribute("aria-checked", String(!c0));
  await page.screenshot({ path: `${DIR}/documents-toggles.png`, fullPage: true });
  // Back to where they were, so later specs see the default.
  await page.getByTestId("auto-resume-toggle").click();
  await page.getByTestId("auto-cover-toggle").click();
  await expect.poll(read).toEqual([r0, c0]);
});
