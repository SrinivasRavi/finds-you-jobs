// The bearer-authenticated loopback transport every RealApi surface shares
// (F-P3). Wire DTOs come from schema.d.ts, so drift is a build error.

import { apiFetch, getSidecarInfo, type SidecarInfo } from "../client";

/** An HTTP error carrying the sidecar status code, so callers can branch on it
 * (e.g. 409 → tombstoned URL → `JobTombstonedError`). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The handshake and the 4 request helpers every surface's client uses. */
export class Transport {
  protected infoP: Promise<SidecarInfo> | null = null;

  protected info(): Promise<SidecarInfo> {
    // Never cache a rejected handshake: `??=` would pin the rejection and every
    // later call would fail even after the sidecar comes up (the Windows
    // white-window bug, 2026-07-19). Clear on failure so the next call retries.
    this.infoP ??= getSidecarInfo().catch((e: unknown) => {
      this.infoP = null;
      throw e;
    });
    return this.infoP;
  }

  protected async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const info = await this.info();
    let res: Response;
    try {
      res = await apiFetch(info, path, init);
    } catch (e) {
      // Network-level failure ("Load failed" in WebKit): the shell may have restarted
      // the sidecar on a new port and token. Drop the cached handshake and retry once,
      // only if the handshake changed: the old port is dead, so nothing double-applies.
      this.infoP = null;
      const fresh = await this.info();
      if (fresh.port === info.port && fresh.token === info.token) throw e;
      res = await apiFetch(fresh, path, init);
    }
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new ApiError(res.status, `${init.method ?? "GET"} ${path} → ${res.status}: ${body}`);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  protected json(method: string, path: string, body: unknown): Promise<unknown> {
    return this.req(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  /** POST a multipart body; the browser sets the boundary. A failure always throws
   *  an `ApiError` with the sidecar's verbatim `detail` and status (D-F13); `what`
   *  names the action for the fallback message. */
  protected async postForm<T>(path: string, form: FormData, what: string): Promise<T> {
    const info = await this.info();
    const res = await apiFetch(info, path, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      let detail = body;
      try {
        const parsed = (JSON.parse(body) as { detail?: unknown }).detail;
        // FastAPI's own 422 is an ARRAY of {loc, msg}, not a string, and
        // string-concatenating it put a literal "[object Object]" in front of
        // the user (found by the url-less e2e, 2026-10-07).
        if (Array.isArray(parsed)) {
          detail = parsed
            .map((e) => {
              const { loc, msg } = e as { loc?: unknown[]; msg?: string };
              const field = Array.isArray(loc) ? loc[loc.length - 1] : undefined;
              return field ? `${String(field)}: ${msg ?? ""}` : (msg ?? "");
            })
            .filter(Boolean)
            .join("; ");
        } else if (typeof parsed === "string") {
          detail = parsed;
        }
      } catch {
        /* non-JSON body — surface as-is */
      }
      throw new ApiError(res.status, detail || `${what} failed (${res.status})`);
    }
    return (await res.json()) as T;
  }
}
