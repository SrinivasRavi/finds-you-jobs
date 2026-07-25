// Unit tests for MutationErrorBanner's state semantics (F-L12): the MAX_STACK
// cap + overflow count, StrictMode double-invoked-updater safety, head-keyed
// auto-dismiss (new arrivals must not reset the head's clock), and dismiss-all.
//
// The banner is driven the same way main.tsx drives it — `fyj:mutation-error`
// CustomEvents on window — so no QueryClient/MutationCache is needed and no
// source export was required. react-i18next is mocked (t → key, with the count
// interpolation made visible) so the real i18n module (top-level await + init)
// stays out of the test graph.
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { count?: number }) =>
      opts?.count != null ? `${key}(${opts.count})` : key,
  }),
}));

import { MUTATION_ERROR_EVENT, MutationErrorBanner } from "./MutationErrorBanner";

const AUTO_DISMISS_MS = 8000; // mirrors the component constant

function fireError(message: string) {
  act(() => {
    window.dispatchEvent(new CustomEvent(MUTATION_ERROR_EVENT, { detail: new Error(message) }));
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("MutationErrorBanner", () => {
  it("renders nothing until an error arrives", () => {
    render(<MutationErrorBanner />);
    expect(screen.queryByTestId("mutation-error-banner")).toBeNull();
    fireError("boom-1");
    expect(screen.queryByTestId("mutation-error-banner")).not.toBeNull();
    expect(screen.queryByText("boom-1")).not.toBeNull();
  });

  it("caps the stack at 3 visible errors and counts the overflow", () => {
    render(<MutationErrorBanner />);
    for (let i = 1; i <= 5; i++) fireError(`boom-${i}`);
    // Latest 3 visible, oldest 2 summarized as a count.
    expect(screen.queryByText("boom-1")).toBeNull();
    expect(screen.queryByText("boom-2")).toBeNull();
    expect(screen.queryByText("boom-3")).not.toBeNull();
    expect(screen.queryByText("boom-4")).not.toBeNull();
    expect(screen.queryByText("boom-5")).not.toBeNull();
    expect(screen.queryByText("shell.mutationError.more(2)")).not.toBeNull();
  });

  it("keeps the overflow count exact under StrictMode's double-invoked updaters", () => {
    // StrictMode (dev) invokes every setState updater twice with the same
    // prev — the single-object PURE updater must be idempotent, or the
    // overflow count inflates (the exact regression the state shape fixed).
    render(
      <StrictMode>
        <MutationErrorBanner />
      </StrictMode>,
    );
    for (let i = 1; i <= 5; i++) fireError(`boom-${i}`);
    expect(screen.getAllByText(/^boom-/)).toHaveLength(3);
    expect(screen.queryByText("shell.mutationError.more(2)")).not.toBeNull();
    expect(screen.queryByText("shell.mutationError.more(4)")).toBeNull();
  });

  it("auto-dismisses oldest-first on the head's own clock — sustained arrivals do not reset it", () => {
    vi.useFakeTimers();
    render(<MutationErrorBanner />);
    fireError("boom-A"); // head — its 8s clock starts now (t=0)
    act(() => {
      vi.advanceTimersByTime(5000); // t=5000 (< 8s later a new error arrives)
    });
    fireError("boom-B");
    expect(screen.queryByText("boom-A")).not.toBeNull();
    expect(screen.queryByText("boom-B")).not.toBeNull();
    // A's clock was NOT reset by B's arrival: A drains at t=8000…
    act(() => {
      vi.advanceTimersByTime(3000); // t=8000
    });
    expect(screen.queryByText("boom-A")).toBeNull();
    expect(screen.queryByText("boom-B")).not.toBeNull();
    // …then B becomes head and gets its own full window (drains at t=16000).
    act(() => {
      vi.advanceTimersByTime(AUTO_DISMISS_MS - 1); // t=15999
    });
    expect(screen.queryByText("boom-B")).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(1); // t=16000
    });
    expect(screen.queryByTestId("mutation-error-banner")).toBeNull();
  });

  it("clears the overflow count when the stack fully drains", () => {
    vi.useFakeTimers();
    render(<MutationErrorBanner />);
    for (let i = 1; i <= 4; i++) fireError(`boom-${i}`); // 3 visible + overflow 1
    expect(screen.queryByText("shell.mutationError.more(1)")).not.toBeNull();
    // Heads drain sequentially: 2, 3, 4 → empty resets the whole state. One
    // act() per hop — each drain's effect (arming the NEXT head's timer) only
    // flushes at the act boundary.
    for (let hop = 0; hop < 3; hop++) {
      act(() => {
        vi.advanceTimersByTime(AUTO_DISMISS_MS);
      });
    }
    expect(screen.queryByTestId("mutation-error-banner")).toBeNull();
    // A fresh error after the drain starts clean — no stale overflow.
    fireError("boom-next");
    expect(screen.queryByText("boom-next")).not.toBeNull();
    expect(screen.queryByText(/shell\.mutationError\.more/)).toBeNull();
  });

  it("dismiss-all clears both the stack and the overflow count", () => {
    render(<MutationErrorBanner />);
    for (let i = 1; i <= 5; i++) fireError(`boom-${i}`);
    fireEvent.click(screen.getByText("shell.mutationError.dismiss"));
    expect(screen.queryByTestId("mutation-error-banner")).toBeNull();
    // And the banner still works after a dismiss — fresh state, no overflow.
    fireError("boom-later");
    expect(screen.queryByText("boom-later")).not.toBeNull();
    expect(screen.queryByText(/shell\.mutationError\.more/)).toBeNull();
  });

  it("stringifies non-Error detail payloads", () => {
    render(<MutationErrorBanner />);
    act(() => {
      window.dispatchEvent(new CustomEvent(MUTATION_ERROR_EVENT, { detail: "plain string" }));
    });
    expect(screen.queryByText("plain string")).not.toBeNull();
  });
});
