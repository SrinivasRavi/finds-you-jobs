// Real sidecar-backed client. Talks to the sidecar over the bearer-authenticated
// loopback API. Wire DTOs come from the generated types (schema.d.ts), so drift is
// a build error, and are mapped here into the view types in types.ts.
// The class is assembled from 1 file per surface under real/ (F-P3).

import type { DevResult } from "./types";
import { NetworkingApi } from "./real/networking";

export { ApiError } from "./real/transport";
export { refusalOf, stubOperation, usdOrNull } from "./real/mappers";

export class RealApi extends NetworkingApi {
  // ── Dev tools (local fault injection — US-DEV-01) ──────────────────────────
  async devExpireCookie(): Promise<DevResult> {
    return (await this.json("POST", "/api/dev/linkedin/expire-cookie", {})) as DevResult;
  }
  async devFailRunning(): Promise<DevResult> {
    return (await this.json("POST", "/api/dev/operations/fail-running", {})) as DevResult;
  }
  async devSeedApplication(): Promise<DevResult> {
    return (await this.json("POST", "/api/dev/seed-application", {})) as DevResult;
  }
}
