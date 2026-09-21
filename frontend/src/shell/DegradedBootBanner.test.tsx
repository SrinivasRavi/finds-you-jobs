// The degraded-boot banner's visibility rule (D27). It must appear only when
// the backend actually started with its scheduler off, and must take itself
// away once work is running again, because a stale "paused" notice is worse
// than none. The query hooks are mocked so no QueryClient is needed and the
// real i18n module (top-level await + init) stays out of the test graph.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const mutate = vi.fn();
let status: { running: boolean; degradedBoot: boolean } | undefined;

vi.mock("../api/queries", () => ({
  useSchedulerStatus: () => ({ data: status }),
  useResumeScheduler: () => ({ mutate, isPending: false }),
}));

import { DegradedBootBanner } from "./DegradedBootBanner";

afterEach(() => {
  cleanup();
  mutate.mockClear();
  status = undefined;
});

describe("DegradedBootBanner", () => {
  it("shows when the boot was degraded and work is paused", () => {
    status = { running: false, degradedBoot: true };
    render(<DegradedBootBanner />);
    expect(screen.getByTestId("degraded-boot-banner")).toBeTruthy();
    expect(screen.getByText("shell.degradedBoot")).toBeTruthy();
  });

  it("stays away on a normal boot", () => {
    status = { running: true, degradedBoot: false };
    render(<DegradedBootBanner />);
    expect(screen.queryByTestId("degraded-boot-banner")).toBeNull();
  });

  it("stays away before the status has loaded", () => {
    status = undefined;
    render(<DegradedBootBanner />);
    expect(screen.queryByTestId("degraded-boot-banner")).toBeNull();
  });

  it("goes away once work is running again, without a dismiss", () => {
    // The resume mutation flips `running`; the banner must not need the user
    // to also dismiss it, or a resumed session keeps claiming to be paused.
    status = { running: true, degradedBoot: true };
    render(<DegradedBootBanner />);
    expect(screen.queryByTestId("degraded-boot-banner")).toBeNull();
  });

  it("resumes background work on the button", () => {
    status = { running: false, degradedBoot: true };
    render(<DegradedBootBanner />);
    fireEvent.click(screen.getByText("shell.degradedBootResume"));
    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it("dismisses without resuming", () => {
    status = { running: false, degradedBoot: true };
    render(<DegradedBootBanner />);
    fireEvent.click(screen.getByText("shell.degradedBootDismiss"));
    expect(screen.queryByTestId("degraded-boot-banner")).toBeNull();
    expect(mutate).not.toHaveBeenCalled();
  });
});
