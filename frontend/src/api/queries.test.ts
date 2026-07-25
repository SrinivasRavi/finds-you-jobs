// Unit tests for makeTrailingThrottle (F-H4) — the leading+trailing throttle
// behind the SSE→Query invalidation bridge — and for the bridge's networker
// candidate lane (roster liveness, restored 2026-07-25): per-candidate
// discover events must grow the referral roster live through their own wider
// throttle group, never per-event and never touching non-roster keys. Driven
// entirely by fake timers (Date + setTimeout are both faked so `elapsed`
// math is exact).
import { QueryClient } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

// The bridge under test reads the singleton event bus and the api client at
// module scope — both are seams we fake: the bus so tests can emit SSE
// envelopes directly, the api so no RealApi (handshake fetch) is constructed.
vi.mock("./index", () => ({
  api: {},
  hasSidecar: () => false,
  makeApi: () => ({}),
}));
vi.mock("./events", () => {
  const eventListeners = new Set<(ev: unknown) => void>();
  const stateListeners = new Set<(s: string) => void>();
  return {
    eventBus: {
      subscribe(onEvent: ((ev: unknown) => void) | null, onState?: (s: string) => void) {
        if (onEvent) eventListeners.add(onEvent);
        if (onState) stateListeners.add(onState);
        return () => {
          if (onEvent) eventListeners.delete(onEvent);
          if (onState) stateListeners.delete(onState);
        };
      },
      emitEvent(ev: unknown) {
        for (const listener of eventListeners) listener(ev);
      },
    },
  };
});

import { eventBus } from "./events";
import { makeTrailingThrottle, useSSEInvalidation } from "./queries";

const bus = eventBus as unknown as { emitEvent: (ev: unknown) => void };

const INTERVAL = 300;

describe("makeTrailingThrottle", () => {
  beforeEach(() => {
    // Default system time (real "now") is far past epoch, so the very first
    // call always sees `elapsed >= intervalMs` and fires on the leading edge.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fires the leading call immediately", () => {
    const fn = vi.fn();
    const throttled = makeTrailingThrottle(fn, INTERVAL);
    throttled();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("collapses a burst into exactly one trailing call, timed from the leading fire", () => {
    const fn = vi.fn();
    const throttled = makeTrailingThrottle(fn, INTERVAL);
    throttled(); // leading — t=0
    vi.advanceTimersByTime(50);
    throttled(); // schedules trailing at t=INTERVAL (INTERVAL - 50 from here)
    vi.advanceTimersByTime(50);
    throttled(); // no-op — trailing already scheduled
    vi.advanceTimersByTime(50);
    throttled(); // no-op
    expect(fn).toHaveBeenCalledTimes(1);
    // Trailing fires exactly at t=INTERVAL, not INTERVAL after the last call.
    vi.advanceTimersByTime(INTERVAL - 150 - 1); // t = INTERVAL - 1
    expect(fn).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1); // t = INTERVAL
    expect(fn).toHaveBeenCalledTimes(2);
    // Quiescence after the burst — nothing else pending.
    vi.advanceTimersByTime(10 * INTERVAL);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("never loses the trailing call when the burst ends between the leading fire and the timer", () => {
    const fn = vi.fn();
    const throttled = makeTrailingThrottle(fn, INTERVAL);
    throttled(); // leading
    vi.advanceTimersByTime(100);
    throttled(); // burst ends here — the final event must still land
    expect(fn).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(INTERVAL - 100);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("cancel() clears the pending trailing call", () => {
    const fn = vi.fn();
    const throttled = makeTrailingThrottle(fn, INTERVAL);
    throttled(); // leading
    vi.advanceTimersByTime(10);
    throttled(); // trailing scheduled
    throttled.cancel();
    vi.advanceTimersByTime(10 * INTERVAL);
    expect(fn).toHaveBeenCalledTimes(1); // only the leading fire
  });

  it("cancel() with nothing pending is a no-op", () => {
    const fn = vi.fn();
    const throttled = makeTrailingThrottle(fn, INTERVAL);
    throttled.cancel();
    expect(fn).not.toHaveBeenCalled();
    throttled();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("works again after cancel()", () => {
    const fn = vi.fn();
    const throttled = makeTrailingThrottle(fn, INTERVAL);
    throttled(); // leading
    vi.advanceTimersByTime(10);
    throttled(); // trailing scheduled
    throttled.cancel();
    vi.advanceTimersByTime(10 * INTERVAL);
    expect(fn).toHaveBeenCalledTimes(1);
    // A fresh call outside the window fires on the leading edge again…
    throttled();
    expect(fn).toHaveBeenCalledTimes(2);
    // …and a burst after that still schedules a (new) trailing call.
    vi.advanceTimersByTime(20);
    throttled();
    vi.advanceTimersByTime(INTERVAL - 20);
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("a call within the window right after a trailing fire schedules another trailing call", () => {
    const fn = vi.fn();
    const throttled = makeTrailingThrottle(fn, INTERVAL);
    throttled(); // leading — t=0
    vi.advanceTimersByTime(100);
    throttled(); // trailing at t=INTERVAL
    vi.advanceTimersByTime(INTERVAL - 100);
    expect(fn).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(50);
    throttled(); // still inside the new window — trailing at t=2*INTERVAL
    expect(fn).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(INTERVAL - 50);
    expect(fn).toHaveBeenCalledTimes(3);
  });
});

// ─── the bridge's networker candidate lane (roster liveness) ─────────────────

const CANDIDATE_WINDOW = 500; // mirrors invalidateRosterCandidates in queries.ts
const ROSTER_KEYS = ["referralCandidates", "contacts", "applications"];

const candidateEvent = {
  type: "networker",
  payload: { phase: "candidate", company: "Acme" },
};
const discoveredEvent = {
  type: "networker",
  payload: { phase: "discovered", company: "Acme" },
};

/** The first element of every queryKey passed to invalidateQueries so far. */
function invalidatedKeys(spy: MockInstance): string[] {
  return spy.mock.calls.map((call) => {
    const arg = call[0] as { queryKey: readonly string[] } | undefined;
    return arg?.queryKey?.[0] ?? "<blanket>";
  });
}

describe("useSSEInvalidation — networker candidate lane", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function mount() {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const rendered = renderHook(() => useSSEInvalidation(qc));
    return { spy, unmount: rendered.unmount };
  }

  it("a candidate event invalidates exactly the roster-scoped keys, live (leading edge)", () => {
    const { spy, unmount } = mount();
    bus.emitEvent(candidateEvent);
    expect(invalidatedKeys(spy)).toEqual(ROSTER_KEYS);
    unmount();
  });

  it("a candidate burst collapses into ONE trailing roster group inside the window", () => {
    const { spy, unmount } = mount();
    bus.emitEvent(candidateEvent); // leading — t=0
    for (let i = 0; i < 9; i++) {
      vi.advanceTimersByTime(30);
      bus.emitEvent(candidateEvent); // all inside the 500 ms window
    }
    expect(invalidatedKeys(spy)).toEqual(ROSTER_KEYS); // still just the leading group
    vi.advanceTimersByTime(CANDIDATE_WINDOW); // trailing fires exactly once
    expect(invalidatedKeys(spy)).toEqual([...ROSTER_KEYS, ...ROSTER_KEYS]);
    // Quiescence — no further invalidations without new events.
    vi.advanceTimersByTime(10 * CANDIDATE_WINDOW);
    expect(spy).toHaveBeenCalledTimes(2 * ROSTER_KEYS.length);
    unmount();
  });

  it("the `discovered` summary keeps its own lane — a candidate burst does not absorb it", () => {
    const { spy, unmount } = mount();
    bus.emitEvent(candidateEvent); // candidate lane, leading
    bus.emitEvent(discoveredEvent); // summary lane, leading — fires immediately
    expect(invalidatedKeys(spy)).toEqual([...ROSTER_KEYS, ...ROSTER_KEYS]);
    unmount();
  });

  it("never touches non-roster keys (archivedContacts/referralQuota stay quiet)", () => {
    const { spy, unmount } = mount();
    bus.emitEvent(candidateEvent);
    vi.advanceTimersByTime(30);
    bus.emitEvent(candidateEvent);
    vi.advanceTimersByTime(10 * CANDIDATE_WINDOW);
    const keys = invalidatedKeys(spy);
    expect(keys).not.toContain("archivedContacts");
    expect(keys).not.toContain("referralQuota");
    expect(keys).not.toContain("ledger");
    unmount();
  });

  it("unmount drops a pending trailing invalidation", () => {
    const { spy, unmount } = mount();
    bus.emitEvent(candidateEvent); // leading
    vi.advanceTimersByTime(30);
    bus.emitEvent(candidateEvent); // trailing scheduled
    unmount();
    vi.advanceTimersByTime(10 * CANDIDATE_WINDOW);
    expect(spy).toHaveBeenCalledTimes(ROSTER_KEYS.length); // only the leading group
  });
});
