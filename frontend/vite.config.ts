import { readFileSync } from "node:fs";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Version for the browser-dev/test path only — the packaged app reads the true
// bundle version through Tauri's getVersion() at runtime (see shell/appVersion.ts).
const pkgVersion = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf8"),
).version as string;

// The Tauri webview loads this build; in the browser-dev path (scripts/dev-web.mjs)
// the sidecar port/token arrive via VITE_SIDECAR_PORT / VITE_SIDECAR_TOKEN from
// .env.local. strictPort keeps the CSP host (127.0.0.1:1420) honest.
// FYJ_WEB_PORT (default 1420) lets a second browser-dev/e2e instance run while
// `pnpm dev` already holds 1420 (2026-07-23); the Tauri path never sets it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(pkgVersion),
  },
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: Number(process.env.FYJ_WEB_PORT ?? 1420),
    strictPort: true,
  },
});
