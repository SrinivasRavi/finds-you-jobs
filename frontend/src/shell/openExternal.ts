// External-link opening (2026-07-11 beta feedback: NO link opened in the
// desktop app — the WebView blocks window.open/target=_blank for external
// origins). Every outbound URL routes through the shell's `open_external`
// command (OS default browser); the browser-dev path falls back to
// window.open. `installExternalLinkInterceptor` makes this automatic for
// every `<a target="_blank">` in the app — no per-link wiring.

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function openExternal(url: string): void {
  if (!/^https?:\/\//i.test(url)) return;
  if (inTauri()) {
    void import("@tauri-apps/api/core").then(({ invoke }) =>
      invoke("open_external", { url }).catch(() => {
        window.open(url, "_blank", "noopener,noreferrer");
      }),
    );
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

/** Open the user's terminal running `claude` so they can log into their Claude
 *  subscription (onboarding's `not_logged_in` guidance). Routes through the
 *  shell's `open_login_terminal` command; a no-op outside the Tauri app (the
 *  browser-dev path has no terminal to open). */
export function openLoginTerminal(): void {
  if (!inTauri()) return;
  void import("@tauri-apps/api/core").then(({ invoke }) =>
    invoke("open_login_terminal").catch(() => {
      /* best-effort: the failure surfaces as Verify still reporting not-logged-in */
    }),
  );
}

/** Capture-phase click handler: any anchor pointing off-app opens in the OS
 *  browser. Installed once at app boot. Returns the uninstaller. */
export function installExternalLinkInterceptor(): () => void {
  const onClick = (ev: MouseEvent) => {
    const anchor = (ev.target as HTMLElement | null)?.closest?.("a[href]");
    if (!(anchor instanceof HTMLAnchorElement)) return;
    const href = anchor.href;
    if (!/^https?:\/\//i.test(href)) return;
    if (new URL(href).origin === window.location.origin) return;
    ev.preventDefault();
    ev.stopPropagation();
    openExternal(href);
  };
  document.addEventListener("click", onClick, true);
  return () => document.removeEventListener("click", onClick, true);
}
