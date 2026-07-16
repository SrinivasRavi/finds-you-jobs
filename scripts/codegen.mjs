// OpenAPI → TypeScript codegen (architecture §4.3).
//
// Dumps the sidecar's OpenAPI schema (via `python -m sidecar.app.openapi_export`)
// and runs `openapi-typescript` into `frontend/src/api/schema.d.ts` — the single
// generated file the A3 track owns under frontend/. Run: `pnpm codegen`.
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const OUT = "frontend/src/api/schema.d.ts";

const schema = execFileSync(
  "uv",
  ["run", "python", "-m", "sidecar.app.openapi_export"],
  { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
);

const dir = mkdtempSync(join(tmpdir(), "fyj-openapi-"));
const specPath = join(dir, "openapi.json");
writeFileSync(specPath, schema);

const bin = join("node_modules", ".bin", "openapi-typescript");
execFileSync(bin, [specPath, "-o", OUT], { stdio: "inherit" });
console.log(`codegen: wrote ${OUT}`);
