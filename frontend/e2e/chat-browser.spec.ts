// Covers: the chat agent's browser tools (audit-2026-10-14.md section 8.11, part B).
// The live sidecar with FYJ_FAKE_LLM: on "click", the fake engine reads the page in
// the LinkedIn view and clicks its first button. The click comes back as a card
// that names the button; the user's OK clicks it in the real surface. The surface
// shows a loopback fixture on VITE_LINKEDIN_ORIGIN, never linkedin.com.

import { createServer, type Server } from "node:http";

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/chat-browser";
const ORIGIN = process.env.VITE_LINKEDIN_ORIGIN ?? "";
const originIsLocalFixture = /^http:\/\/127\.0\.0\.1:\d+\/$/.test(ORIGIN);

let fixture: Server | null = null;

test.beforeAll(async () => {
  if (!originIsLocalFixture) return;
  const port = Number(/:(\d+)\/$/.exec(ORIGIN)![1]);
  fixture = createServer((_req, res) => {
    res.writeHead(200, { "content-type": "text/html" });
    res.end(
      "<html><body style='background:#eef6ff'><h1 id='h'>fixture home feed</h1>" +
        "<button onclick=\"document.getElementById('h').textContent = 'the agent clicked'\">" +
        "Say hello</button></body></html>",
    );
  });
  await new Promise<void>((resolve) => fixture!.listen(port, "127.0.0.1", () => resolve()));
});

test.afterAll(async () => {
  fixture?.close();
});

test("the agent's click in the LinkedIn view waits for the user's OK", async ({
  page,
  request,
}) => {
  test.skip(!originIsLocalFixture, "needs VITE_LINKEDIN_ORIGIN on the loopback fixture");
  const { base, token } = sidecarInfo();
  const headers = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/profile`, {
    headers,
    data: { resume_markdown: "# E2E Candidate" },
  });
  await request.post(`${base}/api/settings`, { headers, data: { voyager_risk_marker_on: true } });
  expect((await request.post(`${base}/api/dev/linkedin/mark-session-valid`, { headers })).ok())
    .toBeTruthy();

  await page.goto("/networking");
  await page.getByTestId("chat-open").click();
  await page.getByTestId("linkedin-state-pill").click();
  await expect(page.getByTestId("linkedin-view")).toBeVisible();
  await expect(page.getByTestId("screencast-page-url")).toHaveText(ORIGIN, { timeout: 30_000 });
  // The view sits beside the drawer, not under it: its close button stays in reach.
  const view = await page.getByRole("dialog").boundingBox();
  const drawer = await page.getByTestId("chat-drawer").boundingBox();
  expect(view!.x + view!.width).toBeLessThanOrEqual(drawer!.x);

  await page.getByTestId("chat-input").fill("click the button on this page");
  await page.getByTestId("chat-input").press("Enter");
  const card = page.getByTestId("chat-card");
  await expect(card).toContainText("Say hello", { timeout: 20_000 });
  await expect(card).toContainText("browser click");
  await page.screenshot({ path: `${DIR}/1-click-card.png` });

  // Nothing ran before the OK.
  const before = await request.get(`${base}/api/browser/page/text`, { headers });
  expect((await before.json()).text).toContain("fixture home feed");

  await page.getByTestId("chat-card-confirm").click();
  await expect(page.getByTestId("chat-card-state")).toHaveText("Done", { timeout: 20_000 });
  const after = await request.get(`${base}/api/browser/page/text`, { headers });
  expect((await after.json()).text).toContain("the agent clicked");
  await page.screenshot({ path: `${DIR}/2-clicked.png` });
});
