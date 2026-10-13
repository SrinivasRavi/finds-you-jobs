// Screencast WebSocket client: the live JPEG stream off the sidecar's Chrome.
// The token rides a query param, as on the SSE bus. Binary messages are frames,
// text messages are JSON control frames one way and navigate/resize the other.
// WebSocket has no retry, so every reopen re-resolves the handshake first (F-H3).
// 1 client per mounted surface: the stream carries a per-viewer viewport.

import { getSidecarInfo } from "./client";

export type ScreencastState = "connecting" | "live" | "reconnecting";

/** Control frames the sidecar interleaves with the binary JPEG frames. A
 *  `status` carries `state`/`surface` on connect and `url` once a navigation
 *  commits, so every field is optional and read by shape. */
export type ScreencastControl =
  | { type: "status"; url?: string; state?: string; surface?: string }
  | { type: "error"; message: string };

/** The frontend's real display geometry, read from `window.screen`. Sent once
 *  the socket opens so the surface lays its page out at the true monitor size
 *  instead of a generic default. */
export interface ViewportMetrics {
  width: number;
  height: number;
  dpr: number;
  colorDepth: number;
}

/** Commands the surface sends back. Each is a last-write-wins statement of
 *  intent, which is what lets a queued one be replaced rather than stacked. */
type ScreencastCommand =
  | { type: "viewport"; width: number; height: number; dpr: number; colorDepth: number }
  | { type: "navigate"; url: string }
  | { type: "resize"; width: number; height: number };

export interface ScreencastHandlers {
  onFrame: (bytes: ArrayBuffer) => void;
  onControl?: (msg: ScreencastControl) => void;
  onState?: (state: ScreencastState) => void;
}

const RECONNECT_BACKOFF_MS = 1000;

export class ScreencastClient {
  private handlers: ScreencastHandlers;
  // Which broker surface this socket attaches to (`?surface=` slug). Omitted →
  // the sidecar's generic `default` surface, which is the dev `/browser` route's
  // behavior. A vendor slug (e.g. LinkedIn's) is supplied by the caller — this
  // transport stays vendor-agnostic (`plugin-architecture.md` section 8.1 rule 5).
  private readonly surface?: string;
  private socket: WebSocket | null = null;
  // Single-flight guard: connect() awaits the sidecar handshake before
  // assigning `socket`, so a React StrictMode double-mount (or a reconnect
  // racing a manual connect) must not open TWO sockets.
  private opening: Promise<void> | null = null;
  private state: ScreencastState = "connecting";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  // Commands issued before the socket opened, keyed by kind so the newest
  // navigate/resize replaces the stale one instead of queueing behind it.
  private queued = new Map<ScreencastCommand["type"], ScreencastCommand>();

  constructor(handlers: ScreencastHandlers, surface?: string) {
    this.handlers = handlers;
    this.surface = surface;
  }

  connect(): void {
    this.closed = false;
    this.handlers.onState?.(this.state);
    if (this.socket || this.opening) return;
    this.open();
  }

  /** Tear the stream down for good — the surface unmounted. Any armed retry is
   *  cancelled, so a closed client never resurrects itself. */
  close(): void {
    this.closed = true;
    if (this.reconnectTimer != null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.queued.clear();
    const ws = this.socket;
    this.socket = null;
    ws?.close();
  }

  sendViewport(metrics: ViewportMetrics): void {
    this.send({ type: "viewport", ...metrics });
  }

  sendNavigate(url: string): void {
    this.send({ type: "navigate", url });
  }

  sendResize(width: number, height: number): void {
    this.send({ type: "resize", width, height });
  }

  private send(cmd: ScreencastCommand): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(cmd));
      return;
    }
    if (this.closed) return;
    this.queued.set(cmd.type, cmd);
  }

  private flush(): void {
    const pending = [...this.queued.values()];
    this.queued.clear();
    for (const cmd of pending) this.send(cmd);
  }

  private setState(next: ScreencastState): void {
    if (this.state === next) return;
    this.state = next;
    this.handlers.onState?.(next);
  }

  private open(): void {
    this.opening = this.ensureOpen()
      // A failed handshake (sidecar still booting, or restarting) retries on
      // the same backoff path as a dropped socket — never leave the stream
      // permanently dark.
      .catch(() => {
        this.setState("reconnecting");
        this.scheduleReconnect();
      })
      .finally(() => {
        this.opening = null;
      });
  }

  private async ensureOpen(): Promise<void> {
    if (this.socket || this.closed) return;
    const info = await getSidecarInfo();
    // close() can land while the handshake is in flight (StrictMode unmount).
    if (this.closed) return;
    const surfaceParam = this.surface
      ? `&surface=${encodeURIComponent(this.surface)}`
      : "";
    const url = `ws://127.0.0.1:${info.port}/api/browser/screencast?token=${encodeURIComponent(info.token)}${surfaceParam}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.socket = ws;
    ws.onopen = () => {
      this.setState("live");
      this.flush();
    };
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        try {
          this.handlers.onControl?.(JSON.parse(e.data) as ScreencastControl);
        } catch {
          /* ignore malformed control frame */
        }
        return;
      }
      if (e.data instanceof ArrayBuffer) this.handlers.onFrame(e.data);
    };
    // No onerror handler: a WebSocket error is always followed by a close, and
    // the error event carries no detail worth surfacing over the close path.
    ws.onclose = () => {
      if (this.socket !== ws) return; // superseded by close() or a rebuild
      this.socket = null;
      if (this.closed) return;
      this.setState("reconnecting");
      this.scheduleReconnect();
    };
  }

  /** Reopen against a re-resolved handshake after a short backoff. Inside Tauri
   *  the handshake re-asks the shell, which answers with the restarted
   *  sidecar's new port + token; in browser dev the env handshake is static, so
   *  this degrades to a plain retry against the same address. */
  private scheduleReconnect(): void {
    if (this.reconnectTimer != null || this.closed) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closed && !this.socket && !this.opening) this.open();
    }, RECONNECT_BACKOFF_MS);
  }
}
