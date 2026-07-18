// Vite launcher with a real lifecycle (2026-07-17 dogfood: Ctrl-C on
// `pnpm dev` left vite holding port 1420, so the next run failed with
// EADDRINUSE and a stale window survived).
//
// Why a wrapper: `tauri dev` kills its beforeDevCommand CHILD (`pnpm --dir
// frontend dev`), but pnpm's vite GRANDCHILD isn't in that kill and gets
// orphaned. This script owns vite directly:
//   - vite runs in its own process group;
//   - SIGINT/SIGTERM/exit kill the whole group (TERM, then KILL after 3 s);
//   - a ppid poll catches the hard-killed-parent case (tauri-cli gone
//     without signalling us — reparented means dead parent) and cleans up.

import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const ORIGINAL_PPID = process.ppid;
const IS_WIN = process.platform === "win32";

// Windows: pnpm is pnpm.cmd (needs a shell to spawn), `cwd` instead of a
// --dir path argument (shell arg-joining breaks on spaces), and no POSIX
// process groups (detached + kill(-pid) are Unix-only; the catch blocks
// below fall through harmlessly).
const vite = spawn("pnpm", ["dev"], {
  cwd: join(ROOT, "frontend"),
  stdio: "inherit",
  shell: IS_WIN,
  detached: !IS_WIN, // its own process group → we can kill the whole tree
});

let closing = false;
function shutdown(code = 0) {
  if (closing) return;
  closing = true;
  try {
    process.kill(-vite.pid, "SIGTERM");
  } catch {
    try {
      vite.kill("SIGTERM"); // Windows (no process groups) or already gone
    } catch {
      /* already gone */
    }
  }
  const hardKill = setTimeout(() => {
    try {
      process.kill(-vite.pid, "SIGKILL");
    } catch {
      /* already gone */
    }
  }, 3000);
  hardKill.unref();
  setTimeout(() => process.exit(code), 3200).unref();
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
process.on("exit", () => {
  try {
    process.kill(-vite.pid, "SIGKILL");
  } catch {
    /* already gone */
  }
});

vite.on("exit", (code) => {
  if (!closing) process.exit(code ?? 0);
});

// Hard-killed parent: we get reparented (ppid changes / goes to 1) without
// ever receiving a signal — poll and clean up so vite never outlives tauri.
setInterval(() => {
  if (process.ppid !== ORIGINAL_PPID || process.ppid === 1) shutdown(0);
}, 2000).unref();
