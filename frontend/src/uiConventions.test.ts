// UI conventions, enforced. 1: no all-caps text; the `uppercase` utility is
// banned. 2: 1 font family; `font-mono` only where content is code-like, and a new
// file wanting it joins the allowlist here deliberately. The test scans the source
// so a regression fails CI.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname);

// Files allowed to use `font-mono` — each for a stated, code-like reason.
const MONO_ALLOWLIST = new Set([
  "shell/MarkdownEditor.tsx", // a markdown editor's textarea
  "shell/mdHtml.ts", // rendered markdown <code>
  "shell/MutationErrorBanner.tsx", // verbatim error detail
  "shell/Avatar.tsx", // fixed-width initials glyphs
  "surfaces/Analytics.tsx", // verbatim error excerpts only (text-bad rows)
  "surfaces/Onboarding.tsx", // the API-key input
  "surfaces/Dev.tsx", // dev-only tooling
  "surfaces/settings/PromptsSection.tsx", // the prompt editor
  "surfaces/settings/PromptTuningModal.tsx", // the same prompt, editable
  "surfaces/settings/DiscoverySources.tsx", // masked API-key hints
]);

// Forward slashes always: the allowlist above is written with them, and
// `join` emits backslashes on Windows.
function relative(path: string): string {
  return path.slice(SRC.length + 1).replaceAll("\\", "/");
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      walk(path, out);
    } else if (/\.(tsx|ts)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(path);
    }
  }
  return out;
}

describe("UI conventions", () => {
  const files = walk(SRC);

  it("bans the `uppercase` utility everywhere (no all-caps text)", () => {
    const offenders = files.filter((f) =>
      /\buppercase\b/.test(readFileSync(f, "utf8")),
    );
    expect(
      offenders.map(relative),
      "all-caps text is banned — restyle these without `uppercase`",
    ).toEqual([]);
  });

  it("allows `font-mono` only in the code-like allowlist (one font family)", () => {
    const offenders = files
      .filter((f) => /\bfont-mono\b/.test(readFileSync(f, "utf8")))
      .map(relative)
      .filter((rel) => !MONO_ALLOWLIST.has(rel));
    expect(
      offenders,
      "font-mono outside the allowlist — the app is one font family; " +
        "if this content is genuinely code-like, add the file here deliberately",
    ).toEqual([]);
  });
});
