// App layout — the 76px rail + main column grid the prototype uses
// (`grid h-screen grid-cols-[76px_1fr]`). Routed surfaces render into <Outlet>.

import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

import i18n from "../i18n";
import { LeftRail } from "./LeftRail";

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

export function Layout() {
  const { t } = useTranslation();
  const fatal = useSidecarFatal();
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
        <Outlet />
      </div>
    </div>
  );
}
