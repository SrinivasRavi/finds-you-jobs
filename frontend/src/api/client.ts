// Typed sidecar client.
//
// A1 hand-writes the minimal handshake + /healthz path. In A3 the generated
// OpenAPI client + types land in this same `src/api/` directory
// (openapi-typescript against the sidecar's /openapi.json — architecture §4.3);
// the app imports its typed operations from here. Keep hand-written helpers that
// sit outside the generated surface (the handshake itself) in this file.

export interface SidecarInfo {
  port: number;
  token: string;
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function fromTauri(): Promise<SidecarInfo | null> {
  if (!inTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  // The shell answers "sidecar not ready" until it has read the PORT/TOKEN
  // handshake off the sidecar's stdout. The webview regularly wins that race
  // (observed on Windows 2026-07-19: white window on roughly every other
  // launch), so poll until the sidecar is up instead of failing the app.
  const deadline = Date.now() + 120_000; // generous: first boot on a slow disk
  for (;;) {
    try {
      const [port, token] = await Promise.all([
        invoke<number>("get_sidecar_port"),
        invoke<string>("get_api_token"),
      ]);
      return { port, token };
    } catch (e) {
      if (Date.now() >= deadline) throw e;
      await new Promise((r) => setTimeout(r, 250));
    }
  }
}

function fromEnv(): SidecarInfo | null {
  const port = import.meta.env.VITE_SIDECAR_PORT;
  const token = import.meta.env.VITE_SIDECAR_TOKEN;
  if (!port || !token) return null;
  return { port: Number(port), token };
}

/**
 * Resolve the sidecar port + bearer token. Inside Tauri this comes from the
 * shell via the `get_sidecar_port` / `get_api_token` commands; in the
 * browser-dev path it falls back to the VITE_SIDECAR_* env vars.
 */
export async function getSidecarInfo(): Promise<SidecarInfo> {
  const info = (await fromTauri()) ?? fromEnv();
  if (!info) {
    throw new Error(
      "sidecar handshake unavailable: not running inside Tauri and " +
        "VITE_SIDECAR_PORT / VITE_SIDECAR_TOKEN are unset",
    );
  }
  return info;
}

/** The base URL for the loopback sidecar API. */
export function apiBase(info: SidecarInfo): string {
  return `http://127.0.0.1:${info.port}`;
}

/** Bearer-authenticated fetch against the sidecar. */
export async function apiFetch(
  info: SidecarInfo,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${info.token}`);
  return fetch(`${apiBase(info)}${path}`, { ...init, headers });
}

export interface Health {
  status: string;
}

export async function fetchHealth(info: SidecarInfo): Promise<Health> {
  const res = await apiFetch(info, "/healthz");
  if (!res.ok) {
    throw new Error(`/healthz returned ${res.status}`);
  }
  return (await res.json()) as Health;
}
