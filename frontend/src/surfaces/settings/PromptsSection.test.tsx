// The 3 per-kind key maps resolve, for every prompt the sidecar registers.
//
// keyResolution.test.ts only scans literal `t("…")` calls, and all 3 of these
// are `t(MAP[kind])`, so nothing else catches a key with no string behind it.
// It renders as the raw key on a tab label, a scope line or a button.

import { describe, expect, it } from "vitest";

import en from "../../i18n/locales/en";
import { RUN_LABEL_KEY } from "./PromptTuningModal";
import { SCOPE_KEY, TITLE_KEY } from "./PromptsSection";

/** The registry's kinds, in its display order (`PROMPT_KINDS`,
 *  sidecar/app/prompt_overrides.py). A kind added there without a string here
 *  shows the user a dotted key. */
const KINDS = ["score", "tailor", "cover", "extract", "networker_draft"];

function resolves(key: string): boolean {
  let node: unknown = en;
  for (const part of key.split(".")) {
    if (typeof node !== "object" || node === null || !(part in node)) return false;
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string";
}

describe("the prompt panes' runtime keys", () => {
  for (const [name, map] of Object.entries({ SCOPE_KEY, TITLE_KEY, RUN_LABEL_KEY })) {
    it(`${name} covers every kind, and every key has a string`, () => {
      expect(Object.keys(map).sort()).toEqual([...KINDS].sort());
      expect(Object.values(map).filter((k) => !resolves(k))).toEqual([]);
    });
  }
});
