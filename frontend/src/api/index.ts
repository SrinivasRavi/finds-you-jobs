// Client switch — architecture §4.3 / ROADMAP A2–A4.
//
// No mocks/ directory exists yet in this rebuild (that lands with its own
// commit) — the app always talks to the real sidecar-backed client
// (src/api/real.ts). `hasSidecar()` stays exported: it's the same handshake
// check main.tsx / the guard routes rely on to know whether a sidecar is
// reachable at all (Tauri webview, or `VITE_SIDECAR_PORT`/`TOKEN` on the
// `pnpm dev:web` path).

import { RealApi } from "./real";

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

/** True when a real sidecar is reachable (Tauri webview or explicit env). */
export function hasSidecar(): boolean {
  const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const envPort = import.meta.env.VITE_SIDECAR_PORT;
  return inTauri || Boolean(envPort);
}

/** Build the client the current environment dictates (A4 switch). Mock mode
 *  returns with the mocks/ commit; until then this is always `RealApi`. */
export function makeApi(): RealApi {
  return new RealApi();
}

// Single shared client instance for the app session. Tests construct their
// own for isolation.
export const api = makeApi();
