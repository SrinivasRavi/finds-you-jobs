// The shared Chromium download hook (A16, A21). A lost `done` event must not
// leave it on "downloading": the status read settles it, and a sidecar that
// restarted mid-download (status `idle`) reads as stopped, so Download returns.

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const h = vi.hoisted(() => ({
  status: { state: "running", message: "" },
  listeners: [] as ((ev: { type: string; payload: Record<string, unknown> }) => void)[],
}));
vi.mock("../api", () => ({
  api: {
    installBrowser: vi.fn(() => Promise.resolve("started")),
    browserInstallStatus: vi.fn(() => Promise.resolve(h.status)),
  },
}));
vi.mock("../api/events", () => ({
  eventBus: {
    subscribe: (fn: (typeof h.listeners)[number]) => {
      h.listeners.push(fn);
      return () => {
        h.listeners = h.listeners.filter((l) => l !== fn);
      };
    },
  },
}));

import { useBrowserInstall } from "./BrowserDownload";

beforeEach(() => {
  vi.useFakeTimers();
  h.status = { state: "running", message: "" };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  h.listeners = [];
});

async function startAndFlush() {
  const hook = renderHook(() => useBrowserInstall());
  await act(async () => {
    hook.result.current.start();
    await Promise.resolve();
  });
  return hook;
}

describe("useBrowserInstall", () => {
  it("settles on the done event", async () => {
    const { result } = await startAndFlush();
    expect(result.current.state).toBe("downloading");
    act(() => h.listeners.forEach((l) => l({ type: "browser_install", payload: { state: "done" } })));
    expect(result.current.state).toBe("ready");
    expect(h.listeners).toHaveLength(0);
  });

  it("settles from the status read when the event never arrives (A21)", async () => {
    const { result } = await startAndFlush();
    h.status = { state: "done", message: "" };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(result.current.state).toBe("ready");
  });

  it("reads a restarted sidecar's idle status as a stopped download", async () => {
    const { result } = await startAndFlush();
    h.status = { state: "idle", message: "" };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(result.current.state).toBe("failed");
    expect(result.current.error).toBe("browser.download.stopped");
  });
});
