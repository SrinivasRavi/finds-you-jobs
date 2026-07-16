// SSE event bus — one EventSource for the whole app (architecture §4.1).
//
// Streams typed `{type, payload}` envelopes from /api/events (token rides as a
// query param — SSE can't set headers; acceptable on the loopback-only surface).
// The browser's native EventSource auto-retry handles reconnection; this bus
// makes that state visible (`connecting → live ⇄ reconnecting`) so the UI can
// render an honest connection indicator and refetch its snapshot after a gap —
// events missed while disconnected are never replayed, so a reconnect must
// re-read state from the API, not trust the stream.

import { getSidecarInfo } from "./client";

export interface SSEEvent {
  type: string;
  payload: Record<string, unknown>;
}

export type StreamState = "connecting" | "live" | "reconnecting";

type EventListener = (ev: SSEEvent) => void;
type StateListener = (state: StreamState) => void;

class EventBus {
  private listeners = new Set<EventListener>();
  private stateListeners = new Set<StateListener>();
  private source: EventSource | null = null;
  // Single-flight guard: ensureOpen awaits the sidecar handshake before
  // assigning `source`, so two near-simultaneous subscribes (React StrictMode
  // double-mount) must not open TWO EventSource connections.
  private opening: Promise<void> | null = null;
  private state: StreamState = "connecting";

  subscribe(onEvent: EventListener | null, onState?: StateListener): () => void {
    if (onEvent) this.listeners.add(onEvent);
    if (onState) {
      this.stateListeners.add(onState);
      onState(this.state);
    }
    if (!this.source && !this.opening) {
      this.opening = this.ensureOpen()
        .catch(() => this.setState("reconnecting"))
        .finally(() => {
          this.opening = null;
        });
    }
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

  private async ensureOpen(): Promise<void> {
    if (this.source) return;
    const info = await getSidecarInfo();
    const url = `http://127.0.0.1:${info.port}/api/events?token=${encodeURIComponent(info.token)}`;
    const es = new EventSource(url);
    this.source = es;
    es.onopen = () => this.setState("live");
    // EventSource retries on its own after an error; surface the gap honestly.
    es.onerror = () => this.setState("reconnecting");
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as SSEEvent;
        for (const fn of this.listeners) fn(ev);
      } catch {
        /* ignore malformed frame */
      }
    };
  }
}

export const eventBus = new EventBus();
