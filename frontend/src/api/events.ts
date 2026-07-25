// SSE event bus — one EventSource for the whole app (architecture §4.1).
//
// Streams typed `{type, payload}` envelopes from /api/events (token rides as a
// query param — SSE can't set headers; acceptable on the loopback-only surface).
// The browser's native EventSource auto-retry handles reconnection; this bus
// makes that state visible (`connecting → live ⇄ reconnecting`) so the UI can
// render an honest connection indicator and refetch its snapshot after a gap —
// events missed while disconnected are never replayed, so a reconnect must
// re-read state from the API, not trust the stream.
//
// Native retry only ever reconnects to the URL the EventSource was built with.
// The shell's supervisor kill-restarts an unhealthy sidecar on a NEW random
// port + token, so after REBUILD_AFTER_ERRORS straight failures the bus tears
// the source down, re-resolves the handshake, and rebuilds — mirroring how
// RealApi.req drops its cached handshake on a network-level failure (F-H3).

import { getSidecarInfo } from "./client";
import type { components } from "./schema";

/** The typed SSE envelope union, generated from the sidecar's Pydantic event
 *  models via `pnpm codegen` (F-M2) — discriminated on `type`, so consumers
 *  narrow instead of casting. Payload extras beyond the load-bearing keys ride
 *  the `[key: string]: unknown` index signature. */
export type SSEEvent = components["schemas"]["SSEEnvelope"];

export type StreamState = "connecting" | "live" | "reconnecting";

type EventListener = (ev: SSEEvent) => void;
type StateListener = (state: StreamState) => void;

// Let the browser's native retry (which targets the original port) fail this
// many times before assuming a restart moved the sidecar and re-resolving the
// handshake. Refused connections fail fast, so this stays a few seconds.
const REBUILD_AFTER_ERRORS = 3;
const REBUILD_BACKOFF_MS = 1000;

class EventBus {
  private listeners = new Set<EventListener>();
  private stateListeners = new Set<StateListener>();
  private source: EventSource | null = null;
  // Single-flight guard: ensureOpen awaits the sidecar handshake before
  // assigning `source`, so two near-simultaneous subscribes (React StrictMode
  // double-mount) must not open TWO EventSource connections.
  private opening: Promise<void> | null = null;
  private state: StreamState = "connecting";
  private errorCount = 0;
  private rebuildTimer: ReturnType<typeof setTimeout> | null = null;

  subscribe(onEvent: EventListener | null, onState?: StateListener): () => void {
    if (onEvent) this.listeners.add(onEvent);
    if (onState) {
      this.stateListeners.add(onState);
      onState(this.state);
    }
    if (!this.source && !this.opening) this.open();
    return () => {
      if (onEvent) this.listeners.delete(onEvent);
      if (onState) this.stateListeners.delete(onState);
    };
  }

  private setState(next: StreamState): void {
    if (this.state === next) return;
    this.state = next;
    for (const fn of this.stateListeners) fn(next);
  }

  private open(): void {
    this.opening = this.ensureOpen()
      // A failed handshake (sidecar still booting) retries on the same backoff
      // path as a moved sidecar — never leave the bus permanently closed.
      .catch(() => {
        this.setState("reconnecting");
        this.scheduleRebuild();
      })
      .finally(() => {
        this.opening = null;
      });
  }

  private async ensureOpen(): Promise<void> {
    if (this.source) return;
    const info = await getSidecarInfo();
    const url = `http://127.0.0.1:${info.port}/api/events?token=${encodeURIComponent(info.token)}`;
    const es = new EventSource(url);
    this.source = es;
    es.onopen = () => {
      this.errorCount = 0;
      this.setState("live");
    };
    // EventSource retries on its own after an error; surface the gap honestly.
    // After a shell-driven restart that retry targets a dead port forever —
    // count the failures and rebuild against a fresh handshake (F-H3).
    es.onerror = () => {
      this.setState("reconnecting");
      this.errorCount += 1;
      if (this.errorCount >= REBUILD_AFTER_ERRORS) this.scheduleRebuild();
    };
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as SSEEvent;
        for (const fn of this.listeners) fn(ev);
      } catch {
        /* ignore malformed frame */
      }
    };
  }

  /** Tear down the (dead) source and reopen against a re-resolved handshake
   *  after a short backoff. Inside Tauri the handshake re-asks the shell, which
   *  answers with the restarted sidecar's new port + token; in browser dev the
   *  env handshake is static, so this degrades to the native-retry behavior. */
  private scheduleRebuild(): void {
    // Guard on the timer ONLY. Checking `this.opening` here looked like a
    // single-flight guard but was a livelock: open()'s `.catch` calls this
    // BEFORE its `.finally` nulls `opening`, so a failed handshake could never
    // arm the retry timer and the bus sat in "reconnecting" forever. The
    // clause guarded nothing real — the only other caller, es.onerror, fires
    // as a macrotask, which cannot run before the `.finally` microtask that
    // clears `opening` (and in the failed-handshake path no EventSource exists
    // to error at all). The timer callback below still re-checks
    // `!this.source && !this.opening` before reopening.
    if (this.rebuildTimer != null) return;
    this.source?.close();
    this.source = null;
    this.errorCount = 0;
    this.rebuildTimer = setTimeout(() => {
      this.rebuildTimer = null;
      if (!this.source && !this.opening) this.open();
    }, REBUILD_BACKOFF_MS);
  }
}

export const eventBus = new EventBus();
