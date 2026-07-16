// Covers: skeleton cold boot — the UI connects to the live sidecar over the
// PORT/TOKEN handshake and renders an honest "connected" state.

import { expect, test } from "@playwright/test";

const DIR = "e2e/_screenshots/boot";

test("shell skeleton reaches a healthy sidecar", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByTestId("sidecar-status")).toHaveText("connected", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("sidecar-port")).toContainText(
    "sidecar healthy on 127.0.0.1:",
  );

  await page.screenshot({ path: `${DIR}/connected.png`, fullPage: true });
});
