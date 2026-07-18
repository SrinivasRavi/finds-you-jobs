// `pnpm dev` / `pnpm dev:web` entry with a self-sufficient PATH (2026-07-17
// fresh-install test: the setup script installs Rust/uv, but a terminal opened
// BEFORE that install has a stale PATH — `cargo metadata: No such file or
// directory` — because installers only wire PATH for new shells). Instead of
// asking users to know that, prepend the standard per-user tool locations to
// this process's PATH before running anything; child processes inherit it.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
import { homedir } from "node:os";

const home = homedir();
const extra = [
  join(home, ".cargo", "bin"), // rustup: cargo
  join(home, ".local", "bin"), // uv's default install location
];
const key = process.platform === "win32" ? "Path" : "PATH";
const current = process.env[key] ?? process.env.PATH ?? "";
const parts = current.split(delimiter);
for (const dir of extra) {
  if (existsSync(dir) && !parts.includes(dir)) parts.unshift(dir);
}
process.env[key] = parts.join(delimiter);
process.env.PATH = process.env[key];

// Usage: node scripts/dev.mjs [web]
const web = process.argv[2] === "web";

function run(cmd, args) {
  return new Promise((resolve) => {
    // shell on Windows: pnpm is pnpm.cmd there, and Node can't spawn .cmd
    // files directly (ENOENT — observed on a real install 2026-07-18). With a
    // shell, pass ONE command string — an args array there is deprecated
    // (DEP0190) and would break on values needing escaping.
    const isWin = process.platform === "win32";
    const child = isWin
      ? spawn([cmd, ...args].join(" "), { stdio: "inherit", env: process.env, shell: true })
      : spawn(cmd, args, { stdio: "inherit", env: process.env });
    // Lifecycle: forward Ctrl-C/kill to the child, and if OUR parent dies
    // without signalling us (reparented), take the child down too — the same
    // no-orphans discipline as scripts/dev-frontend.mjs.
    const forward = (sig) => {
      try {
        child.kill(sig);
      } catch {
        /* already gone */
      }
    };
    const onInt = () => forward("SIGINT");
    const onTerm = () => forward("SIGTERM");
    process.on("SIGINT", onInt);
    process.on("SIGTERM", onTerm);
    const originalPpid = process.ppid;
    const orphanPoll = setInterval(() => {
      if (process.ppid !== originalPpid || process.ppid === 1) forward("SIGTERM");
    }, 2000);
    orphanPoll.unref();
    child.on("exit", (code, signal) => {
      process.off("SIGINT", onInt);
      process.off("SIGTERM", onTerm);
      clearInterval(orphanPoll);
      resolve(signal ? 1 : (code ?? 0));
    });
    child.on("error", (err) => {
      console.error(`could not run ${cmd}: ${err.message}`);
      resolve(1);
    });
  });
}

const codegen = await run("node", ["scripts/codegen.mjs"]);
if (codegen !== 0) process.exit(codegen);
process.exit(
  web
    ? await run("node", ["scripts/dev-web.mjs"])
    : await run("pnpm", ["tauri", "dev"]),
);
