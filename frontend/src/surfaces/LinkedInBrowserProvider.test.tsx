// The app-level LinkedIn browser modal (2026-08-16): opened via context from
// any surface, maximum-size dialog (the tailored-resume sizing), the live
// surface + queue panel inside, session status chip in the title bar, honest
// disabled body when Referral Outreach is off. The heavy children are
// stand-ins (their own tests cover them); the provider, the modal shell, and
// the open/close mechanics are the real code.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  session: { enabled: true, status: "valid" } as Record<string, unknown>,
  browserOps: { current: null, queued: [], settled: [] } as {
    current: unknown;
    queued: unknown[];
    settled: unknown[];
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
  initReactI18next: { type: "3rdParty", init: () => {} },
}));
vi.mock("../api/queries", () => ({
  useLinkedInSession: () => ({ data: h.session }),
}));
vi.mock("./BrowserSurface", () => ({
  BrowserSurface: (props: { surface?: string; origin?: string; autoHome?: boolean }) => (
    <div
      data-testid="fake-browser-surface"
      data-surface={props.surface}
      data-auto-home={String(props.autoHome)}
    />
  ),
}));
vi.mock("./BrowserOpPlan", () => ({
  useBrowserOps: () => h.browserOps,
  opBusy: (op: { state?: string } | null) =>
    op != null && !["succeeded", "failed", "cancelled"].includes(op.state ?? ""),
  BrowserOpPlan: () => <aside data-testid="fake-op-plan" />,
}));

import { LinkedInBrowserProvider, useLinkedInBrowser } from "./LinkedInBrowserProvider";

function Opener() {
  const { open, isOpen } = useLinkedInBrowser();
  return (
    <button data-testid="opener" data-open={String(isOpen)} onClick={() => open()}>
      open
    </button>
  );
}

/** An opener that arrives "from" another modal, so it hands over a way back. */
function OpenerWithBack({ onBack }: { onBack: () => void }) {
  const { open } = useLinkedInBrowser();
  return (
    <button data-testid="opener-with-back" onClick={() => open({ onBack })}>
      open
    </button>
  );
}

function renderProvider() {
  return render(
    <LinkedInBrowserProvider>
      <Opener />
    </LinkedInBrowserProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  h.session = { enabled: true, status: "valid" };
  h.browserOps = { current: null, queued: [], settled: [] };
});

describe("LinkedInBrowserProvider", () => {
  it("offers no way back when opened directly", () => {
    // Opened from the Networking header there is nowhere to return to, and a
    // dead Back button is worse than none.
    const { getByTestId, queryByTestId } = renderProvider();
    fireEvent.click(getByTestId("opener"));
    expect(queryByTestId("linkedin-modal-back")).toBeNull();
  });

  it("returns to the caller when opened from another modal", () => {
    // The referrals popup closes itself to get here (the browser dialog
    // replaces it rather than stacking), so Back has to reopen it.
    const onBack = vi.fn();
    const { getByTestId, queryByTestId } = render(
      <LinkedInBrowserProvider>
        <OpenerWithBack onBack={onBack} />
      </LinkedInBrowserProvider>,
    );
    fireEvent.click(getByTestId("opener-with-back"));
    fireEvent.click(getByTestId("linkedin-modal-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(queryByTestId("linkedin-view")).toBeNull();
  });

  it("mounts nothing until opened, then shows the surface + queue panel", () => {
    renderProvider();
    expect(screen.queryByTestId("linkedin-view")).toBeNull();
    expect(screen.queryByTestId("fake-browser-surface")).toBeNull();

    fireEvent.click(screen.getByTestId("opener"));
    expect(screen.getByTestId("opener").getAttribute("data-open")).toBe("true");
    expect(screen.getByTestId("linkedin-view")).toBeTruthy();
    const surface = screen.getByTestId("fake-browser-surface");
    expect(surface.getAttribute("data-surface")).toBe("linkedin");
    expect(surface.getAttribute("data-auto-home")).toBe("true");
    expect(screen.getByTestId("fake-op-plan")).toBeTruthy();
    // Title-bar status chip (the modal's own chrome, shared semantics).
    expect(screen.getByTestId("linkedin-modal-status").textContent).toBe(
      "networking.linkedinPill.connected",
    );
  });

  it("a running op defers auto-home (the op owns the page)", () => {
    h.browserOps = {
      current: { id: "op1", kind: "send", state: "running" },
      queued: [],
      settled: [],
    };
    renderProvider();
    fireEvent.click(screen.getByTestId("opener"));
    expect(
      screen.getByTestId("fake-browser-surface").getAttribute("data-auto-home"),
    ).toBe("false");
  });

  it("closes on Escape and unmounts the surface", () => {
    renderProvider();
    fireEvent.click(screen.getByTestId("opener"));
    expect(screen.getByTestId("linkedin-view")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("linkedin-view")).toBeNull();
    expect(screen.queryByTestId("fake-browser-surface")).toBeNull();
    expect(screen.getByTestId("opener").getAttribute("data-open")).toBe("false");
  });

  it("shows the honest one-liner when Referral Outreach is off", () => {
    h.session = { enabled: false, status: "never_set" };
    renderProvider();
    fireEvent.click(screen.getByTestId("opener"));
    expect(screen.getByTestId("linkedin-view-disabled")).toBeTruthy();
    expect(screen.queryByTestId("fake-browser-surface")).toBeNull();
  });
});
