// The one-time Chromium download (A16), shared by every surface that needs a
// browser: Export, the Applier and the LinkedIn browser (applier/window.md
// section 4.6). The stream never replays a missed event, so while a download
// runs this also reads the sidecar's own status, and a lost `done` can't leave
// the UI on "Downloading" forever (A21).

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api";
import { eventBus } from "../api/events";

export type BrowserInstallState = "idle" | "downloading" | "ready" | "failed";

const STATUS_POLL_MS = 3000;

export function useBrowserInstall() {
  const { t } = useTranslation();
  const [state, setState] = useState<BrowserInstallState>("idle");
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef<(() => void) | null>(null);

  const settle = useCallback((next: BrowserInstallState, message: string | null = null) => {
    stopRef.current?.();
    stopRef.current = null;
    setState(next);
    setError(message);
  }, []);

  useEffect(() => () => stopRef.current?.(), []);

  const start = useCallback(() => {
    stopRef.current?.();
    setState("downloading");
    setError(null);
    let timer: ReturnType<typeof setInterval> | null = null;
    const unsubscribe = eventBus.subscribe((ev) => {
      if (ev.type !== "browser_install") return;
      if (ev.payload.state === "done") settle("ready");
      else if (ev.payload.state === "failed") settle("failed", ev.payload.message ?? null);
    });
    stopRef.current = () => {
      unsubscribe();
      if (timer != null) clearInterval(timer);
    };
    const check = async () => {
      try {
        const s = await api.browserInstallStatus();
        if (s.state === "done") settle("ready");
        else if (s.state === "failed") settle("failed", s.message || null);
        // "idle" mid-download: the sidecar restarted and the download died with it.
        else if (s.state === "idle") settle("failed", t("browser.download.stopped"));
      } catch {
        /* the sidecar is restarting; the next tick asks again */
      }
    };
    void api
      .installBrowser()
      .then(() => {
        if (stopRef.current == null) return; // settled by the event already
        timer = setInterval(() => void check(), STATUS_POLL_MS);
      })
      .catch((e: unknown) => settle("failed", e instanceof Error ? e.message : String(e)));
  }, [settle, t]);

  const reset = useCallback(() => settle("idle"), [settle]);

  return { state, error, start, reset };
}

/** The Download strip: why a browser is needed, the button, then progress, then
 *  `readyText`. `onReady` fires once when the download lands. */
export function BrowserDownloadStrip({
  missingText,
  readyText,
  onReady,
  testId = "browser-download",
}: {
  missingText: string;
  readyText?: string;
  onReady?: () => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  const install = useBrowserInstall();
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  useEffect(() => {
    if (install.state === "ready") onReadyRef.current?.();
  }, [install.state]);
  return (
    <div className="flex flex-col gap-1.5 text-[12px] text-ink-2" data-testid={testId}>
      {install.state === "downloading" ? (
        <span>{t("browser.download.downloading")}</span>
      ) : install.state === "ready" ? (
        readyText ? <span>{readyText}</span> : null
      ) : (
        <>
          <span>{missingText}</span>
          <button
            data-testid={`${testId}-btn`}
            onClick={install.start}
            className="inline-flex h-[26px] w-fit items-center rounded-7 border border-border-2 bg-surface px-2.5 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
          >
            {t("browser.download.button")}
          </button>
        </>
      )}
      {install.error ? <span className="break-words text-ink-4">{install.error}</span> : null}
    </div>
  );
}
