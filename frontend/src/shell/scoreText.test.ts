import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";

import { asReasons, scoreText } from "./scoreText";

const t = ((key: string, opts?: Record<string, unknown>) =>
  opts ? `${key} ${JSON.stringify(opts)}` : key) as unknown as TFunction;

describe("scoreText (audit section 5)", () => {
  it("leaves a stored English line as it is", () => {
    expect(scoreText("Strong Python match.", t)).toBe("Strong Python match.");
  });

  it("words a nested line first, and an unstated fact as 'unstated'", () => {
    const line = {
      key: "kw.location",
      args: { why: { key: "loc.city", args: { city: "Pune" } }, years: null },
      text: "Location: Pune is a location you asked for.",
    };
    expect(scoreText(line, t)).toBe(
      'scoreReason.kw.location {"why":"scoreReason.loc.city {\\"city\\":\\"Pune\\",' +
        '\\"defaultValue\\":\\"loc.city\\"}","years":"scoreReason.unstated",' +
        '"defaultValue":"Location: Pune is a location you asked for."}',
    );
  });

  it("keeps strings and coded lines, and drops anything else", () => {
    expect(asReasons(["a", { key: "kw.skills", args: {} }, 3, null])).toHaveLength(2);
    expect(asReasons(undefined)).toEqual([]);
  });
});
