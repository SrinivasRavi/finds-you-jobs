// Browser-dev path: run the sidecar + vite together WITHOUT the Tauri shell.
//
// Mirrors, in Node, exactly what the Rust shell does at runtime: spawn the
// sidecar, read the PORT=/TOKEN= handshake off stdout, then hand those to the
// frontend. Here they go into frontend/.env.local (VITE_SIDECAR_*), which the
// client's env fallback reads (frontend/src/api/client.ts). This is the path
// used for Playwright screenshots when a full Tauri build isn't available.
//
// Usage: `pnpm dev:web` (from repo root). Ctrl-C tears both down.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { writeFileSync, rmSync } from "node:fs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const envLocalPath = join(repoRoot, "frontend", ".env.local");

const children = [];

let closing = false;
function shutdown(code = 0) {
  if (closing) return;
  closing = true;
  for (const child of children) {
    // Per-child try/catch: a dead child (ESRCH on its stale pgid) must never
    // abort the loop and leave the NEXT child unsignalled (2026-07-25).
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      try {
        child.kill("SIGTERM");
      } catch {
        /* already gone */
      }
    }
  }
  try {
    rmSync(envLocalPath);
  } catch {
    /* already gone */
  }
  process.exit(code);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

// Hard-killed parent (reparented without a signal): clean up the children
// rather than orphaning vite + the sidecar (2026-07-17 dogfood).
const ORIGINAL_PPID = process.ppid;
setInterval(() => {
  if (process.ppid !== ORIGINAL_PPID || process.ppid === 1) shutdown(0);
}, 2000).unref();

// Piped (never inherited) stdio on BOTH children: when Playwright drives this
// script as its webServer, our stdout/stderr are Playwright's pipes, and its
// teardown waits for them to CLOSE. A child that inherits those fds keeps the
// pipe open past our death and hangs the Playwright CLI forever (2026-07-25:
// observed 6 h — teardown SIGKILLs only OUR process group, cleanup never runs).
const sidecar = spawn("uv", ["run", "python", "-m", "sidecar.app"], {
  cwd: repoRoot,
  detached: true,
  stdio: ["ignore", "pipe", "pipe"],
  // The sidecar's orphan watchdog watches THIS pid (2026-07-17): if this
  // script is hard-killed (SIGKILL — Playwright teardown, a crashed shell),
  // the sidecar reaps itself within a poll tick instead of squatting on the
  // port with the `uv` wrapper.
  // FYJ_DEV unlocks the /api/dev/* fault-injection endpoints (F-L4) — the
  // browser-dev/Playwright path is exactly what they exist for; the packaged
  // app never sets it, so those routes 404 there.
  env: { ...process.env, FYJ_SHELL_PID: String(process.pid), FYJ_DEV: "1" },
});
children.push(sidecar);
sidecar.stderr.on("data", (chunk) => process.stderr.write(chunk));

let port;
let token;
let started = false;
let buffer = "";

sidecar.stdout.on("data", (chunk) => {
  buffer += chunk.toString();
  let idx;
  while ((idx = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    const portMatch = /^PORT=(\d+)$/.exec(line);
    const tokenMatch = /^TOKEN=(.+)$/.exec(line);
    if (portMatch) port = portMatch[1];
    else if (tokenMatch) token = tokenMatch[1];
    else if (line) process.stdout.write(`[sidecar] ${line}\n`);

    if (port && token && !started) {
      started = true;
      writeFileSync(
        envLocalPath,
        `VITE_SIDECAR_PORT=${port}\nVITE_SIDECAR_TOKEN=${token}\n`,
      );
      console.log(`[dev-web] sidecar up on ${port}; starting vite…`);
      // Vite runs under scripts/dev-frontend.mjs — the same wrapper `tauri
      // dev` uses. Its ppid poll is the vite-side counterpart of the
      // sidecar's FYJ_SHELL_PID watchdog: if THIS process is hard-killed
      // (Playwright's webServer teardown SIGKILLs our process group without
      // ever signalling us), the reparented wrapper reaps vite's whole
      // process group within a poll tick instead of orphaning it on the port
      // (2026-07-25 — the 6 h Playwright teardown hang). The wrapper handles
      // the win32 pnpm.cmd/no-process-group cases itself.
      const vite = spawn(
        process.execPath,
        [join(repoRoot, "scripts", "dev-frontend.mjs")],
        {
          detached: process.platform !== "win32",
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
      children.push(vite);
      vite.stdout.on("data", (chunk) => process.stdout.write(chunk));
      vite.stderr.on("data", (chunk) => process.stderr.write(chunk));
      vite.on("exit", (code) => shutdown(code ?? 0));
    }
  }
});

sidecar.on("exit", (code) => {
  if (!started) {
    console.error(`[dev-web] sidecar exited before handshake (code ${code})`);
    shutdown(1);
  } else if (!closing) {
    // Deliberate: vite stays up. The e2e reconnect specs (zz-reconnect,
    // zzz-reconnect-newport) kill this sidecar mid-run and respawn their own
    // — zzz even page.goto()s through vite AFTER this one is dead.
    console.log(
      `[dev-web] sidecar exited (code ${code}); vite stays up (reconnect specs respawn their own sidecar) — Ctrl-C to quit`,
    );
  }
});
