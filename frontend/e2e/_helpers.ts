// Shared e2e helpers. Not a spec: Playwright's default testMatch only picks up
// *.spec.ts, so this file is imported, never run.
//
// `sidecarInfo` existed as 21 byte-identical copies across the suite, 2 of them
// returning a different shape under the same name, which is how a helper ends
// up meaning 2 things. One copy now, both shapes named.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { APIRequestContext } from "@playwright/test";

const E2E_DIR = dirname(fileURLToPath(import.meta.url));

/** The live sidecar's port and token, read off the handshake `dev-web.mjs`
 *  writes into `frontend/.env.local`. */
export function sidecarHandshake(): { port: string; token: string } {
  // An isolated run (a second vite on FYJ_WEB_PORT beside the maintainer's dev
  // session) passes the handshake by env, because dev-web's .env.local is
  // absent there. Only 4 of the 21 copies of this function had that fallback,
  // so the other 17 specs could not run that way at all.
  const envPort = process.env.VITE_SIDECAR_PORT;
  const envToken = process.env.VITE_SIDECAR_TOKEN;
  if (envPort && envToken) return { port: envPort, token: envToken };
  const env = readFileSync(join(E2E_DIR, "..", ".env.local"), "utf8");
  const port = /VITE_SIDECAR_PORT=(\d+)/.exec(env)?.[1];
  const token = /VITE_SIDECAR_TOKEN=(.+)/.exec(env)?.[1];
  if (!port || !token) throw new Error(".env.local missing sidecar handshake");
  return { port, token };
}

/** The same handshake as a loopback base URL, which is what most specs want. */
export function sidecarInfo(): { base: string; token: string } {
  const { port, token } = sidecarHandshake();
  return { base: `http://127.0.0.1:${port}`, token };
}

/** The bearer header every authed request carries. */
export function auth(token: string): { Authorization: string } {
  return { Authorization: `Bearer ${token}` };
}

/** Get past onboarding: most surfaces only render once a master profile
 *  exists. Idempotent, so a `beforeEach` can call it on every test. */
export async function seedProfile(
  request: APIRequestContext,
  resumeMarkdown = "# E2E Candidate\n\nBackend engineer.",
): Promise<void> {
  const { base, token } = sidecarInfo();
  await request.post(`${base}/api/profile`, {
    headers: auth(token),
    data: { resume_markdown: resumeMarkdown },
  });
}
