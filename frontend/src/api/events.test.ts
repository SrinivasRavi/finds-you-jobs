// Unit tests for the EventBus rebuild path (F-H3): a rejected handshake must
// arm the backoff timer (the fixed scheduleRebuild livelock), repeated
// failures retry once per backoff window (no tight spin), and a successful
// open resets the consecutive-error count.
//
// Mocked seams: `getSidecarInfo` (./client) so each test scripts handshake
// outcomes, and the global EventSource (jsdom has none) with an inspectable
// fake. The bus singleton is re-imported fresh per test via vi.resetModules;
// timers are vitest fake timers so REBUILD_BACKOFF_MS is stepped exactly.
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("./client", () => ({
  getSidecarInfo: vi.fn(),
}));

const REBUILD_BACKOFF_MS = 1000; // mirrors events.ts
const REBUILD_AFTER_ERRORS = 3; // mirrors events.ts

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
}

/** Fresh bus + mocked handshake from the same (reset) module registry. The
 *  mocked client module instance survives vi.resetModules (mock factories run
 *  once), so the shared vi.fn is reset here to keep per-test call counts. */
async function importFresh() {
  const client = await import("./client");
  const getSidecarInfo = client.getSidecarInfo as Mock;
  getSidecarInfo.mockReset();
  const events = await import("./events");
  return { eventBus: events.eventBus, getSidecarInfo };
}

describe("EventBus rebuild scheduling", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  });
  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("a rejected handshake goes to reconnecting and arms the rebuild timer (no dead bus)", async () => {
    const { eventBus, getSidecarInfo } = await importFresh();
    getSidecarInfo.mockRejectedValue(new Error("sidecar still booting"));
    const states: string[] = [];
    eventBus.subscribe(null, (s) => states.push(s));
    expect(states).toEqual(["connecting"]); // initial state replayed to the subscriber
    await vi.advanceTimersByTimeAsync(0); // let the handshake rejection propagate
    expect(states).toContain("reconnecting");
    expect(getSidecarInfo).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(0);
    // The retry timer IS armed (the pre-fix livelock left the bus stuck here
    // forever): nothing before the backoff elapses…
    await vi.advanceTimersByTimeAsync(REBUILD_BACKOFF_MS - 1);
    expect(getSidecarInfo).toHaveBeenCalledTimes(1);
    // …and exactly one re-resolve when it does.
    await vi.advanceTimersByTimeAsync(1);
    expect(getSidecarInfo).toHaveBeenCalledTimes(2);
  });

  it("repeated handshake failures retry once per backoff window — no tight spin", async () => {
    const { eventBus, getSidecarInfo } = await importFresh();
    getSidecarInfo.mockRejectedValue(new Error("still down"));
    eventBus.subscribe(null, () => {});
    await vi.advanceTimersByTimeAsync(0);
    expect(getSidecarInfo).toHaveBeenCalledTimes(1);
    // 3 full backoff windows → exactly 3 more attempts, one per window.
    await vi.advanceTimersByTimeAsync(3 * REBUILD_BACKOFF_MS);
    expect(getSidecarInfo).toHaveBeenCalledTimes(4);
    // Once the sidecar comes back, the next window's retry connects for real.
    getSidecarInfo.mockResolvedValue({ port: 4321, token: "tok" });
    const states: string[] = [];
    eventBus.subscribe(null, (s) => states.push(s));
    await vi.advanceTimersByTimeAsync(REBUILD_BACKOFF_MS);
    expect(FakeEventSource.instances).toHaveLength(1);
    const es = FakeEventSource.instances[0];
    expect(es.url).toContain("4321");
    es.onopen!();
    expect(states.at(-1)).toBe("live");
  });

  it("rebuilds against a RE-RESOLVED handshake after enough straight stream errors", async () => {
    const { eventBus, getSidecarInfo } = await importFresh();
    getSidecarInfo.mockResolvedValue({ port: 1111, token: "tok-1" });
    const states: string[] = [];
    eventBus.subscribe(null, (s) => states.push(s));
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeEventSource.instances).toHaveLength(1);
    const es1 = FakeEventSource.instances[0];
    expect(es1.url).toContain("1111");
    expect(es1.url).toContain("token=tok-1");
    es1.onopen!();
    expect(states.at(-1)).toBe("live");
    // The shell restarted the sidecar on a new port + token.
    getSidecarInfo.mockResolvedValue({ port: 2222, token: "tok-2" });
    for (let i = 0; i < REBUILD_AFTER_ERRORS; i++) es1.onerror!();
    expect(states.at(-1)).toBe("reconnecting");
    // Threshold reached → the dead source is torn down and, after the backoff,
    // a new source is built against the fresh handshake.
    expect(es1.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(REBUILD_BACKOFF_MS);
    expect(FakeEventSource.instances).toHaveLength(2);
    const es2 = FakeEventSource.instances[1];
    expect(es2.url).toContain("2222");
    expect(es2.url).toContain("token=tok-2");
    es2.onopen!();
    expect(states.at(-1)).toBe("live");
  });

  it("a successful open resets the consecutive-error count", async () => {
    const { eventBus, getSidecarInfo } = await importFresh();
    getSidecarInfo.mockResolvedValue({ port: 1111, token: "tok" });
    eventBus.subscribe(null, () => {});
    await vi.advanceTimersByTimeAsync(0);
    const es1 = FakeEventSource.instances[0];
    es1.onopen!();
    // Two errors (below threshold), then a successful native reconnect…
    es1.onerror!();
    es1.onerror!();
    es1.onopen!(); // errorCount resets to 0
    // …then two more errors: still below threshold — NOT 4 cumulative.
    es1.onerror!();
    es1.onerror!();
    await vi.advanceTimersByTimeAsync(10 * REBUILD_BACKOFF_MS);
    expect(es1.closed).toBe(false); // never torn down
    expect(FakeEventSource.instances).toHaveLength(1); // never rebuilt
    expect(getSidecarInfo).toHaveBeenCalledTimes(1); // handshake never re-resolved
    // The third straight error crosses the threshold and tears it down.
    es1.onerror!();
    expect(es1.closed).toBe(true);
  });

  it("delivers parsed events to subscribers and ignores malformed frames", async () => {
    const { eventBus, getSidecarInfo } = await importFresh();
    getSidecarInfo.mockResolvedValue({ port: 1111, token: "tok" });
    const seen: unknown[] = [];
    eventBus.subscribe((ev) => seen.push(ev));
    await vi.advanceTimersByTimeAsync(0);
    const es = FakeEventSource.instances[0];
    es.onopen!();
    es.onmessage!({ data: '{"type":"linkedin","payload":{"phase":"connected"}}' });
    es.onmessage!({ data: "not json {" }); // must not throw or kill the stream
    es.onmessage!({ data: '{"type":"operation","payload":{"kind":"scan","state":"succeeded"}}' });
    expect(seen).toHaveLength(2);
    expect(seen[0]).toMatchObject({ type: "linkedin" });
    expect(seen[1]).toMatchObject({ type: "operation" });
  });
});
