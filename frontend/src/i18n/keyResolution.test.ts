// Every literal translation key has an English string behind it. A missing key
// renders as the key, and nothing else catches it (found when 8 call sites asked
// for a `common.` prefix that a spread namespace doesn't have). Literal `t("…")`
// keys only; a runtime-built key is checked by its feature's own test.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import en from "./locales/en";

const SRC = join(__dirname, "..");
const KEY = /\bt\(\s*"([A-Za-z0-9_.]+)"/g;

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      if (entry !== "i18n") sources(path, out);
    } else if (/\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path)) {
      out.push(path);
    }
  }
  return out;
}

/** i18next resolves a count key through its plural suffixes, so `a.b` is
 *  present when `a.b_one` is. Anything else is a miss. */
function resolves(key: string): boolean {
  const parts = key.split(".");
  const last = parts.pop()!;
  let node: unknown = en;
  for (const part of parts) {
    if (typeof node !== "object" || node === null || !(part in node)) return false;
    node = (node as Record<string, unknown>)[part];
  }
  if (typeof node !== "object" || node === null) return false;
  const leaf = node as Record<string, unknown>;
  return last in leaf || Object.keys(leaf).some((k) => k.startsWith(`${last}_`));
}

describe("translation keys", () => {
  it("every literal t() key resolves in the English bundle", () => {
    const missing: string[] = [];
    for (const file of sources(SRC)) {
      const src = readFileSync(file, "utf8");
      for (const match of src.matchAll(KEY)) {
        if (!resolves(match[1])) missing.push(`${match[1]}  (${file.slice(SRC.length + 1)})`);
      }
    }
    expect(missing).toEqual([]);
  });
});
