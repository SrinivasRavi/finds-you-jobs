// Browser surface (browser broker, commit 1) — paints the sidecar's headless
// Chrome into a canvas over the screencast WebSocket, with a URL bar to drive
// it. Dev-facing for now: hidden from the left rail, reachable at /browser,
// plain strings rather than i18n keys until it becomes a user surface.

import { useEffect, useRef, useState } from "react";

import {
  ScreencastClient,
  type ScreencastControl,
  type ScreencastState,
} from "../api/screencast";

const DEFAULT_URL = "https://example.com";

export function BrowserSurface() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const clientRef = useRef<ScreencastClient | null>(null);
  const [url, setUrl] = useState(DEFAULT_URL);
  const [state, setState] = useState<ScreencastState>("connecting");
  const [frames, setFrames] = useState(0);
  const [pageUrl, setPageUrl] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    // Newest-frame-wins decode. createImageBitmap is async and the stream runs
    // at video rates, so awaiting every frame in arrival order would paint an
    // ever-growing lag behind the live page. Only the newest undecoded frame is
    // held; anything older is dropped on the floor.
    let latest: ArrayBuffer | null = null;
    let decoding = false;
    let disposed = false;

    async function paintLatest(): Promise<void> {
      if (decoding) return;
      decoding = true;
      try {
        while (latest && !disposed) {
          const bytes = latest;
          latest = null;
          const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
          const canvas = canvasRef.current;
          const ctx = canvas?.getContext("2d");
          if (!canvas || !ctx || disposed) {
            bitmap.close();
            continue;
          }
          // The backing store tracks the frame, so a viewport change repaints
          // at the new size instead of stretching the old one.
          if (canvas.width !== bitmap.width) canvas.width = bitmap.width;
          if (canvas.height !== bitmap.height) canvas.height = bitmap.height;
          ctx.drawImage(bitmap, 0, 0);
          bitmap.close();
        }
      } finally {
        decoding = false;
      }
    }

    const client = new ScreencastClient({
      onFrame: (bytes) => {
        latest = bytes;
        setFrames((n) => n + 1);
        void paintLatest();
      },
      onControl: (msg: ScreencastControl) => {
        if (msg.type === "status") {
          if (msg.url) setPageUrl(msg.url);
          setError("");
        } else {
          setError(msg.message);
        }
      },
      onState: (next: ScreencastState) => {
        setState(next);
        // The instant the socket is live, hand the surface our real display
        // geometry (this webview's own window.screen) so it lays the page out at
        // the true monitor size, before the first navigation. Re-sent on every
        // reconnect, since a fresh socket faces a fresh surface.
        if (next === "live") {
          client.sendViewport({
            width: screen.width,
            height: screen.height,
            dpr: window.devicePixelRatio,
            colorDepth: screen.colorDepth,
          });
        }
      },
    });
    clientRef.current = client;
    client.connect();
    return () => {
      disposed = true;
      client.close();
      clientRef.current = null;
    };
  }, []);

  useEffect(() => {
    // Keep the remote viewport the size of the box we paint it into. Queued
    // client-side until the socket opens, so the first observation still lands.
    const el = viewportRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      const width = Math.round(el.clientWidth);
      const height = Math.round(el.clientHeight);
      if (width > 0 && height > 0) clientRef.current?.sendResize(width, height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <>
      <header className="flex min-h-[48px] items-center gap-3 border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">Browser</h1>
        <form
          className="flex flex-1 items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            clientRef.current?.sendNavigate(url);
          }}
        >
          <input
            data-testid="browser-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com"
            className="w-full max-w-xl rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink focus:border-accent focus:outline-none"
          />
          <button
            type="submit"
            data-testid="browser-go"
            className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
          >
            Go
          </button>
        </form>
        <span className="text-[12px] text-ink-3">
          <span data-testid="screencast-status">{state}</span> ·{" "}
          <span data-testid="frame-count">{frames}</span> frames
        </span>
      </header>
      <main className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-4">
        {error ? (
          <p className="text-[12.5px] text-bad" data-testid="screencast-error">
            {error}
          </p>
        ) : null}
        <div
          ref={viewportRef}
          className="min-h-0 flex-1 overflow-hidden rounded-xl border border-border bg-canvas"
        >
          <canvas
            ref={canvasRef}
            data-testid="screencast-canvas"
            className="h-full w-full object-contain"
          />
        </div>
        <p className="truncate text-[12px] text-ink-3" data-testid="screencast-page-url">
          {pageUrl || "no page yet"}
        </p>
      </main>
    </>
  );
}
