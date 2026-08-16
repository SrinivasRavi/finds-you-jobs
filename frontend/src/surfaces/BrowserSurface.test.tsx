// Unit tests for the WATCH-ONLY BrowserSurface (maintainer, 2026-08-16): the
// read-only bar follows every server-pushed `status{url}` (driver-driven and
// SPA navigations alike) — there is no typable URL field, no Go button, and no
// way to steer the surface from here. Auto-home still holds: a pageless attach
// opens the surface's `origin` (deferred while `autoHome` is off), so the
// canvas is never dead blank. The ScreencastClient is mocked (no socket, no
// frames); react-i18next is mocked t→key per the established pattern.

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ScreencastControl, ScreencastHandlers } from "../api/screencast";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// Capture each mounted surface's handlers so tests can drive the control
// channel the way the sidecar would.
const h = vi.hoisted(() => ({
  instances: [] as { handlers: ScreencastHandlers; surface?: string }[],
  sendNavigate: vi.fn(),
}));
vi.mock("../api/screencast", () => ({
  ScreencastClient: class {
    constructor(handlers: ScreencastHandlers, surface?: string) {
      h.instances.push({ handlers, surface });
    }
    connect(): void {}
    close(): void {}
    sendViewport(): void {}
    sendResize(): void {}
    sendNavigate(url: string): void {
      h.sendNavigate(url);
    }
  },
}));

import { BrowserSurface } from "./BrowserSurface";

beforeAll(() => {
  // jsdom has no ResizeObserver; the component only needs observe/disconnect.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe(): void {}
      disconnect(): void {}
      unobserve(): void {}
    },
  );
});

afterEach(() => {
  cleanup();
  h.instances.length = 0;
  h.sendNavigate.mockClear();
});

function pushControl(msg: ScreencastControl): void {
  act(() => {
    h.instances[0]!.handlers.onControl?.(msg);
  });
}

function urlLine(): HTMLElement {
  return screen.getByTestId("screencast-page-url");
}

const ORIGIN = "https://www.linkedin.com/";

describe("BrowserSurface watch-only URL line", () => {
  it("shows the honest no-page state, then follows every server push", () => {
    render(<BrowserSurface />);
    expect(urlLine().textContent).toBe("browser.noPage");

    pushControl({ type: "status", url: "https://example.test/one" });
    expect(urlLine().textContent).toBe("https://example.test/one");

    // A second push (an SPA route change / driver navigation) keeps tracking.
    pushControl({ type: "status", url: "https://example.test/two" });
    expect(urlLine().textContent).toBe("https://example.test/two");
  });

  it("offers no way to steer the surface: no URL input, no Go", () => {
    render(<BrowserSurface surface="pane" origin={ORIGIN} />);
    expect(screen.queryByTestId("browser-url")).toBeNull();
    expect(screen.queryByTestId("browser-go")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});

describe("BrowserSurface auto-home", () => {
  function renderFrozen(autoHome?: boolean) {
    return render(<BrowserSurface surface="pane" origin={ORIGIN} autoHome={autoHome} />);
  }

  it("auto-opens the origin when the attach status reports no page", () => {
    renderFrozen();
    pushControl({ type: "status", state: "streaming", surface: "pane", url: "" });
    expect(h.sendNavigate).toHaveBeenCalledWith(ORIGIN);
  });

  it("never auto-opens over a page the surface already has", () => {
    renderFrozen();
    pushControl({
      type: "status",
      state: "streaming",
      surface: "pane",
      url: "https://www.linkedin.com/feed/",
    });
    expect(h.sendNavigate).not.toHaveBeenCalled();
  });

  it("defers the auto-open while autoHome is off, then opens when it turns on", () => {
    const view = renderFrozen(false);
    pushControl({ type: "status", state: "streaming", surface: "pane", url: "" });
    expect(h.sendNavigate).not.toHaveBeenCalled();

    // The add-on's operation released the lane: autoHome flips back on.
    view.rerender(<BrowserSurface surface="pane" origin={ORIGIN} autoHome={true} />);
    expect(h.sendNavigate).toHaveBeenCalledWith(ORIGIN);
  });

  it("a surface with no origin never auto-opens", () => {
    render(<BrowserSurface />);
    pushControl({ type: "status", state: "streaming", surface: "default", url: "" });
    expect(h.sendNavigate).not.toHaveBeenCalled();
  });
});
