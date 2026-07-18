// OpenAPI → TypeScript codegen (architecture §4.3).
//
// Dumps the sidecar's OpenAPI schema (via `python -m sidecar.app.openapi_export`)
// and runs `openapi-typescript` into `frontend/src/api/schema.d.ts` — the single
// generated file the A3 track owns under frontend/. Run: `pnpm codegen`.
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

const OUT = "frontend/src/api/schema.d.ts";

const schema = execFileSync(
  "uv",
  ["run", "python", "-m", "sidecar.app.openapi_export"],
  { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
);

const dir = mkdtempSync(join(tmpdir(), "fyj-openapi-"));
const specPath = join(dir, "openapi.json");
writeFileSync(specPath, schema);

// Run the package's JS entry with the current Node, not the node_modules/.bin
// shim — the shim is a Unix shell script, so spawning it on Windows is ENOENT
// (observed on a real install 2026-07-18). The bin path isn't in the package's
// exports map, so resolve the package root (via its package.json) and join.
const require = createRequire(import.meta.url);
const pkgRoot = dirname(require.resolve("openapi-typescript/package.json"));
const cli = join(pkgRoot, require("openapi-typescript/package.json").bin["openapi-typescript"]);
execFileSync(process.execPath, [cli, specPath, "-o", OUT], { stdio: "inherit" });
console.log(`codegen: wrote ${OUT}`);
