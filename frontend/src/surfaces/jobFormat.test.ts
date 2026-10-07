import { describe, expect, it } from "vitest";

import { methodColor, scoreRingColor, SCORE_METHODS } from "./jobFormat";

/** The 2-ring score dial (maintainer, 2026-10-07). The outer arc is the score
 *  on a continuous red-to-green ramp; the inner ring is the method. */
describe("scoreRingColor", () => {
  it("is red at 0, amber at 50 and green at 100", () => {
    expect(scoreRingColor(0)).toBe("rgb(220 38 38)");
    expect(scoreRingColor(50)).toBe("rgb(217 119 6)");
    expect(scoreRingColor(100)).toBe("rgb(5 150 105)");
  });

  it("interpolates, so 69 and 70 are not different kinds of job", () => {
    expect(scoreRingColor(69)).not.toBe(scoreRingColor(70));
  });

  it("gets greener as the score rises, across the whole range", () => {
    const green = (s: number) => Number(scoreRingColor(s).match(/\d+/g)![1]);
    for (let s = 50; s < 100; s += 5) expect(green(s + 5)).toBeGreaterThan(green(s));
  });

  it("clamps rather than extrapolating past either end", () => {
    expect(scoreRingColor(-20)).toBe(scoreRingColor(0));
    expect(scoreRingColor(150)).toBe(scoreRingColor(100));
  });
});

describe("methodColor", () => {
  it("gives each of the 4 methods its own colour", () => {
    const colors = SCORE_METHODS.map(methodColor);
    expect(new Set(colors).size).toBe(SCORE_METHODS.length);
  });

  it("reads an unknown method as keyword grey rather than throwing", () => {
    // A newer sidecar can name a method this build has never heard of. The
    // board has to draw something.
    expect(methodColor("something-new")).toBe(methodColor("keyword"));
  });
});
