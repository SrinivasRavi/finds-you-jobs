// App layout — the 76px rail + main column grid the prototype uses
// (`grid h-screen grid-cols-[76px_1fr]`). Routed surfaces render into <Outlet>.

import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { eventBus, type StreamState } from "../api/events";
import i18n from "../i18n";
import { LeftRail } from "./LeftRail";
import { MutationErrorBanner } from "./MutationErrorBanner";
import {
  autoUpdateCheckEnabled,
  checkForUpdate,
  updaterAvailable,
  type CheckResult,
} from "./updater";

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

type AvailableUpdate = Extract<CheckResult, { available: true }>;

/** One quiet update check at launch, only when the user opted in (About pane).
 *  A found update surfaces a dismissible banner; a failed check stays silent —
 *  the manual "Check for updates" button is the deliberate path. */
function useLaunchUpdateCheck(): AvailableUpdate | null {
  const [update, setUpdate] = useState<AvailableUpdate | null>(null);
  useEffect(() => {
    if (!updaterAvailable() || !autoUpdateCheckEnabled()) return;
    let alive = true;
    void checkForUpdate()
      .then((result) => {
        if (alive && result.available) setUpdate(result);
      })
      .catch(() => {
        /* a launch-time check failure is not worth interrupting the user */
      });
    return () => {
      alive = false;
    };
  }, []);
  return update;
}

function UpdateBanner({ update }: { update: AvailableUpdate }) {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(false);
  const [installing, setInstalling] = useState(false);
  if (dismissed) return null;
  return (
    <div
      className="flex items-center gap-3 border-b border-accent/30 bg-accent-wash px-4 py-1.5 text-[12.5px] text-accent-ink"
      data-testid="update-available-banner"
    >
      <span className="flex-1">{t("shell.updateBanner.available", { version: update.version })}</span>
      <button
        type="button"
        data-testid="update-banner-install"
        disabled={installing}
        onClick={() => {
          setInstalling(true);
          void update.install().catch(() => setInstalling(false));
        }}
        className="rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-white transition-colors hover:bg-accent/90 disabled:opacity-60"
      >
        {installing ? t("shell.updateBanner.installing") : t("shell.updateBanner.install")}
      </button>
      <button
        type="button"
        data-testid="update-banner-dismiss"
        onClick={() => setDismissed(true)}
        className="text-ink-3 hover:text-ink"
        aria-label={t("shell.updateBanner.dismiss")}
      >
        <span aria-hidden="true">✕</span>
      </button>
    </div>
  );
}

export function Layout() {
  const { t } = useTranslation();
  const fatal = useSidecarFatal();
  const stream = useStreamState();
  const update = useLaunchUpdateCheck();
  return (
    <div className="grid h-screen grid-cols-[76px_1fr] overflow-hidden bg-canvas">
      <LeftRail />
      <div className="flex min-h-0 flex-col overflow-hidden">
        {update ? <UpdateBanner update={update} /> : null}
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
