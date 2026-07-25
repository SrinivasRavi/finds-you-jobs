// App layout — the 76px rail + main column grid the prototype uses
// (`grid h-screen grid-cols-[76px_1fr]`). Routed surfaces render into <Outlet>.

import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { eventBus, type StreamState } from "../api/events";
import i18n from "../i18n";
import { LeftRail } from "./LeftRail";
import { MutationErrorBanner } from "./MutationErrorBanner";

/** Listen for the Tauri shell's sidecar supervision events. The shell emitted
 *  `sidecar://fatal` (backend killed, supervisor gave up) into a void — the UI
 *  kept rendering cached data while every request silently died as "Load
 *  failed" (maintainer 2026-07-22). No-op outside Tauri (browser dev). */
function useSidecarFatal(): string {
  const [fatal, setFatal] = useState("");
  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void import("@tauri-apps/api/event").then(({ listen }) =>
      listen<{ message: string }>("sidecar://fatal", (e) => {
        setFatal(e.payload.message || i18n.t("shell.backendStoppedFallback"));
      }).then((un) => {
        if (disposed) un();
        else unlisten = un;
      }),
    );
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);
  return fatal;
}

/** The SSE bus's honest connection state (F-M5) — previously visible only on
 *  /dev, so an outage/reconnect was invisible on every main surface while the
 *  board/tracker silently stopped repainting. */
function useStreamState(): StreamState {
  const [stream, setStream] = useState<StreamState>("connecting");
  useEffect(() => eventBus.subscribe(null, setStream), []);
  return stream;
}

export function Layout() {
  const { t } = useTranslation();
  const fatal = useSidecarFatal();
  const stream = useStreamState();
  return (
    <div className="grid h-screen grid-cols-[76px_1fr] overflow-hidden bg-canvas">
      <LeftRail />
      <div className="flex min-h-0 flex-col overflow-hidden">
        {fatal ? (
          <div
            className="border-b border-bad bg-bad-wash px-4 py-2 text-[12.5px] text-bad"
            data-testid="sidecar-fatal-banner"
          >
            {t("shell.sidecarFatalBanner", { message: fatal })}
          </div>
        ) : null}
        {/* Slim reconnect strip — only during a real gap ("connecting" is the
            normal boot handshake; the fatal banner supersedes it). Recovery
            refetches happen in useSSEInvalidation, this is just the honest
            indicator. */}
        {!fatal && stream === "reconnecting" ? (
          <div
            className="border-b border-border bg-surface px-4 py-1.5 text-[12px] text-ink-3"
            data-testid="stream-reconnecting-banner"
          >
            {t("shell.streamReconnecting")}
          </div>
        ) : null}
        <Outlet />
      </div>
      <MutationErrorBanner />
    </div>
  );
}
