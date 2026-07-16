import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// The Tauri webview loads this build; in the browser-dev path (scripts/dev-web.mjs)
// the sidecar port/token arrive via VITE_SIDECAR_PORT / VITE_SIDECAR_TOKEN from
// .env.local. strictPort keeps the CSP host (127.0.0.1:1420) honest.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
});
