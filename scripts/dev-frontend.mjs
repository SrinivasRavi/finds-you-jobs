// Vite launcher with a real lifecycle. `tauri dev` kills its pnpm child but not
// pnpm's vite grandchild, which then held port 1420. This owns vite in its own
// process group, kills the group on SIGINT, SIGTERM or exit (TERM, then KILL after
// 3 s), and a ppid poll cleans up when the parent was hard-killed.

import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const ORIGINAL_PPID = process.ppid;
const IS_WIN = process.platform === "win32";

// Windows: pnpm is pnpm.cmd (needs a shell; one command string — an args
// array with a shell is deprecated, DEP0190), `cwd` instead of a --dir path
// argument (shell arg-joining breaks on spaces), and no POSIX process groups
// (detached + kill(-pid) are Unix-only; the catch blocks below fall through).
const vite = IS_WIN
  ? spawn("pnpm dev", { cwd: join(ROOT, "frontend"), stdio: "inherit", shell: true })
  : spawn("pnpm", ["dev"], {
      cwd: join(ROOT, "frontend"),
      stdio: "inherit",
      detached: true, // its own process group → we can kill the whole tree
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
