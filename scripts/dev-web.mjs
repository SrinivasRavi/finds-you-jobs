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

function shutdown(code = 0) {
  for (const child of children) {
    if (!child.killed) {
      try {
        process.kill(-child.pid, "SIGTERM");
      } catch {
        child.kill("SIGTERM");
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

const sidecar = spawn("uv", ["run", "python", "-m", "sidecar.app"], {
  cwd: repoRoot,
  detached: true,
  stdio: ["ignore", "pipe", "inherit"],
  // The sidecar's orphan watchdog watches THIS pid (2026-07-17): if this
  // script is hard-killed (SIGKILL — Playwright teardown, a crashed shell),
  // the sidecar reaps itself within a poll tick instead of squatting on the
  // port with the `uv` wrapper.
  env: { ...process.env, FYJ_SHELL_PID: String(process.pid) },
});
children.push(sidecar);

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
      const vite = spawn("pnpm", ["dev"], {
        cwd: join(repoRoot, "frontend"),
        detached: true,
        stdio: "inherit",
      });
      children.push(vite);
      vite.on("exit", (code) => shutdown(code ?? 0));
    }
  }
});

sidecar.on("exit", (code) => {
  if (!started) {
    console.error(`[dev-web] sidecar exited before handshake (code ${code})`);
    shutdown(1);
  }
});
