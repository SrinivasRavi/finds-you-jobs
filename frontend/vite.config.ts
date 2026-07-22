import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// The Tauri webview loads this build; in the browser-dev path (scripts/dev-web.mjs)
// the sidecar port/token arrive via VITE_SIDECAR_PORT / VITE_SIDECAR_TOKEN from
// .env.local. strictPort keeps the CSP host (127.0.0.1:1420) honest.
// FYJ_WEB_PORT (default 1420) lets a second browser-dev/e2e instance run while
// `pnpm dev` already holds 1420 (2026-07-23); the Tauri path never sets it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: Number(process.env.FYJ_WEB_PORT ?? 1420),
    strictPort: true,
  },
});
