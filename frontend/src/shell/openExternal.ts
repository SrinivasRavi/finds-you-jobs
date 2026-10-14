// External links. The WebView blocks window.open for external origins, so every
// outbound URL goes through the shell's `open_external` (the OS browser), falling
// back to window.open in dev. The interceptor covers every `<a target="_blank">`.

import { inTauri } from "./host";

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

/** Open the user's terminal running the named subscription CLI's login flow so
 *  they can sign in (onboarding's `not_logged_in` guidance). The shell command
 *  maps the id through an allowlist (claude | codex | agy) — JS never passes a
 *  raw command line. A no-op outside the Tauri app (the browser-dev path has
 *  no terminal to open). */
export function openLoginTerminal(cli: "claude" | "codex" | "agy" = "claude"): void {
  if (!inTauri()) return;
  void import("@tauri-apps/api/core").then(({ invoke }) =>
    invoke("open_login_terminal", { cli }).catch(() => {
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
