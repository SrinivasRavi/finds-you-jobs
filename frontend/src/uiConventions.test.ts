// UI conventions, ENFORCED (maintainer rules, 2026-08-16):
//
// 1. No all-caps text, anywhere. The Tailwind `uppercase` utility is banned —
//    an all-caps heading reads like shouting and broke the app's type rhythm
//    (the "REFERRAL OUTREACH AGENT" panel title incident).
// 2. One font family. `font-mono` is allowed only where the content is
//    genuinely code-like (editors, verbatim error excerpts, an API-key
//    field); everywhere else the app is one face. New files wanting mono must
//    be added to the allowlist here, deliberately.
//
// This test scans the source so a regression fails CI, not a design review.

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
  "surfaces/settings/DiscoverySources.tsx", // masked API-key hints
]);

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
      offenders.map((f) => f.slice(SRC.length + 1)),
      "all-caps text is banned — restyle these without `uppercase`",
    ).toEqual([]);
  });

  it("allows `font-mono` only in the code-like allowlist (one font family)", () => {
    const offenders = files
      .filter((f) => /\bfont-mono\b/.test(readFileSync(f, "utf8")))
      .map((f) => f.slice(SRC.length + 1))
      .filter((rel) => !MONO_ALLOWLIST.has(rel));
    expect(
      offenders,
      "font-mono outside the allowlist — the app is one font family; " +
        "if this content is genuinely code-like, add the file here deliberately",
    ).toEqual([]);
  });
});
