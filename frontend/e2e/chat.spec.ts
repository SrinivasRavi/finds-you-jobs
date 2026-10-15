// Covers: the chat agent's drawer (audit-2026-10-14.md section 8.11, D91). The
// live sidecar with FYJ_FAKE_LLM: the fake engine reads the applications for a
// question, and calls reach_out on "invite", which autopilot-off turns into a card.

import { expect, test } from "@playwright/test";
import { sidecarInfo } from "./_helpers";

const DIR = "e2e/_screenshots/chat";

test("the drawer answers with a tool and asks before a send", async ({ page, request }) => {
  const { base, token } = sidecarInfo();
  const auth = { Authorization: `Bearer ${token}` };
  await request.post(`${base}/api/settings`, {
    headers: auth,
    data: { autopilot_invite: false, autopilot_dm: false },
  });

  await page.goto("/jobs");
  await expect(page.getByTestId("chat-drawer")).toHaveCount(0); // closed by default
  await page.getByTestId("chat-open").click();
  const drawer = page.getByTestId("chat-drawer");
  await expect(drawer).toBeVisible();
  await page.screenshot({ path: `${DIR}/1-empty.png` });

  await page.getByTestId("chat-input").fill("How many applications do I have?");
  await page.getByTestId("chat-input").press("Enter");
  await expect(page.getByTestId("chat-user")).toHaveText("How many applications do I have?");
  await expect(page.getByTestId("chat-agent").last()).toContainText(
    "Done: list_applications answered.",
    { timeout: 15_000 },
  );
  await expect(page.getByTestId("chat-tool-line")).toContainText("list applications");
  await page.screenshot({ path: `${DIR}/2-answered.png` });

  await page.getByTestId("chat-input").fill("invite them");
  await page.getByTestId("chat-send").click();
  const card = page.getByTestId("chat-card");
  await expect(card).toContainText("autopilot is off", { timeout: 15_000 });
  await expect(card).toContainText("reach out");
  await page.screenshot({ path: `${DIR}/3-card.png` });

  await page.getByTestId("chat-card-dismiss").click();
  await expect(page.getByTestId("chat-card-state")).toHaveText("Dismissed");
  await page.screenshot({ path: `${DIR}/4-dismissed.png` });

  // The drawer stays over other screens, and closes.
  await page.goto("/applications");
  await expect(drawer).toHaveCount(0); // a reload starts closed
  await page.getByTestId("chat-open").click();
  await expect(page.getByTestId("chat-history")).toBeVisible();
  await page.getByTestId("chat-close").click();
  await expect(drawer).toHaveCount(0);
});

test("the chat model is picked in LLM Providers", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByText("Assistant model")).toBeVisible();
  await page.getByText("Assistant model").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${DIR}/5-settings-model.png`, fullPage: true });
});
