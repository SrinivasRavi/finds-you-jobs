// Client switch (architecture section 4.3). The app always talks to the real
// sidecar-backed client (real.ts). `hasSidecar()` is the handshake check main.tsx
// and the guard routes use to know whether a sidecar is reachable.

import { inTauri } from "../shell/host";
import { RealApi } from "./real";

/** True when a real sidecar is reachable (Tauri webview or explicit env). */
export function hasSidecar(): boolean {
  const envPort = import.meta.env.VITE_SIDECAR_PORT;
  return inTauri() || Boolean(envPort);
}

/** Build the client the current environment dictates (A4 switch). Mock mode
 *  returns with the mocks/ commit; until then this is always `RealApi`. */
export function makeApi(): RealApi {
  return new RealApi();
}

// Single shared client instance for the app session. Tests construct their
// own for isolation.
export const api = makeApi();
