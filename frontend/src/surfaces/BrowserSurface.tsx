// Browser surface: paints the sidecar's Chrome into a canvas over the screencast
// socket. Vendor-agnostic: `surface` names the broker surface (default: the dev
// route's `default`). Watch-only: the bar shows the live URL, so nobody can
// interrupt an operation by typing. A pageless surface auto-opens the `origin`
// its add-on passes; this component names no site itself.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ScreencastClient,
  type ScreencastControl,
  type ScreencastState,
} from "../api/screencast";

export function BrowserSurface({
  surface,
  origin,
  autoHome = true,
}: {
  surface?: string;
  origin?: string;
  autoHome?: boolean;
}) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const clientRef = useRef<ScreencastClient | null>(null);
  const [state, setState] = useState<ScreencastState>("connecting");
  const [frames, setFrames] = useState(0);
  const [pageUrl, setPageUrl] = useState("");
  const [error, setError] = useState("");
  // Set when the attach status says the surface has no page yet ("" URL);
  // cleared the moment any page URL lands. While set (and `autoHome` allows),
  // the effect below opens the frozen origin so the canvas is never dead blank.
  const [needsHome, setNeedsHome] = useState(false);
  // The socket effect reads these through refs so prop changes never tear the
  // socket down (its dependency is the surface slug alone).
  const originRef = useRef(origin);
  originRef.current = origin;

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
          if (msg.url) {
            setPageUrl(msg.url);
            setNeedsHome(false);
          } else if (msg.state === "streaming" && originRef.current) {
            // The attach status says the surface still sits on its launch
            // about:blank — with a frozen origin that means "open home"
            // (gated on `autoHome` by the effect below, so it never yanks a
            // surface an operation is about to drive).
            setNeedsHome(true);
          }
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
    }, surface);
    clientRef.current = client;
    client.connect();
    return () => {
      disposed = true;
      client.close();
      clientRef.current = null;
    };
  }, [surface]);

  useEffect(() => {
    // Auto-open the origin over a pageless surface, so the canvas is never blank.
    // `needsHome` comes only from the attach status, so a page already somewhere is
    // never moved; `autoHome={false}` defers it while an add-on op runs. Idempotent.
    if (origin && autoHome && needsHome) {
      clientRef.current?.sendNavigate(origin);
    }
  }, [origin, autoHome, needsHome]);

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
      {/* The bar is a READ-ONLY view of where the surface is (maintainer,
          2026-08-16): users watch the referral outreach agent, they don't
          steer it — a typable bar invited mid-operation navigation and the
          unexpected states that come with it. No heading either; where this
          surface mounts, the tab above already names it. */}
      <header className="flex min-h-[48px] items-center gap-3 border-b border-border bg-surface px-5">
        <span
          className="min-w-0 flex-1 truncate text-[12.5px] text-ink-2"
          data-testid="screencast-page-url"
          title={pageUrl || undefined}
        >
          {pageUrl || t("browser.noPage")}
        </span>
        <span className="shrink-0 text-[12px] text-ink-3">
          {/* `state` stays the raw enum ("live" / "connecting"): tests and the
              broker's own vocabulary assert on it, so it is not translated. */}
          <span data-testid="screencast-status">{state}</span> ·{" "}
          <span data-testid="frame-count">{frames}</span> {t("browser.framesLabel")}
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
      </main>
    </>
  );
}
