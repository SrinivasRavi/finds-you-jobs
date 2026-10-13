// Browser-dev path: the sidecar and vite without the Tauri shell. Does what the
// shell does: spawn the sidecar, read PORT=/TOKEN= off stdout, and hand them to
// the frontend via frontend/.env.local (read by src/api/client.ts). The Playwright
// path. `pnpm dev:web`; Ctrl-C stops both.

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
  // The sidecar's orphan watchdog watches this pid, so a hard-killed script takes
  // the sidecar down within a tick. FYJ_DEV unlocks /api/dev/* (F-L4), which exist
  // for this path; the packaged app never sets it.
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
      // Vite runs under scripts/dev-frontend.mjs, like `tauri dev`. Its ppid poll is
      // the vite side of the sidecar's watchdog: Playwright's teardown SIGKILLs our
      // group without a signal, and the wrapper then reaps vite instead of orphaning it
      // on the port. It handles the win32 cases itself.
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
