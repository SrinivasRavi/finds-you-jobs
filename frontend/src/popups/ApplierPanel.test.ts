// The Applier feed lines for a Submit (audit-2026-10-14.md section 8.10):
// autopilot's click is credited to autopilot, never to "you".
import { describe, expect, it } from "vitest";

import "../i18n";
import { formatApplyEvent } from "./ApplierPanel";

const submitted = (extra: Record<string, unknown>) =>
  formatApplyEvent({ event: "apply.user_submitted", clicked: true, ...extra });

describe("formatApplyEvent, a Submit", () => {
  it("credits autopilot for a confirmed autopilot Submit", () => {
    const line = submitted({ by: "autopilot", confirmed: true });
    expect(line?.text).toBe("Submit autopilot clicked Submit, and the page confirmed it.");
    expect(line?.tone).toBe("ok");
  });

  it("says autopilot won't click again when the page is in doubt", () => {
    const line = submitted({ by: "autopilot", confirmed: false, unconfirmed: true });
    expect(line?.text).toContain("Autopilot won't click again");
    expect(line?.tone).toBe("warn");
  });

  it("names a missed button as autopilot's", () => {
    const line = submitted({ by: "autopilot", confirmed: false, clicked: false });
    expect(line?.text).toContain("Submit autopilot couldn't find");
    expect(line?.tone).toBe("bad");
  });

  it("keeps the user's own Submit worded for the user", () => {
    expect(submitted({ by: "user", confirmed: true })?.text).toBe(
      "Clicked Submit for you, and the page confirmed it.",
    );
  });
});
