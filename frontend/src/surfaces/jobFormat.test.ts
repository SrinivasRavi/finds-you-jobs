import { describe, expect, it } from "vitest";

import type { Job } from "../api/types";
import { methodColor, placeLine, placeTitle, scoreRingColor, SCORE_METHODS } from "./jobFormat";

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

/** 1 format for a job's places on every surface (place-picker.md section 8.3). */
describe("placeLine", () => {
  const job = (location: string, places: string[], work_style: Job["work_style"] = "") => ({
    location,
    places,
    work_style,
  });

  it("prints the resolved place, then the work style", () => {
    expect(placeLine(job("Bangalore - Engineering", ["Bengaluru, Karnataka, India"], "HYBRID")))
      .toBe("Bengaluru, Karnataka, India (Hybrid)");
  });

  it("names the first place and counts the rest", () => {
    expect(placeLine(job("SF; NYC", ["San Francisco, California, United States",
      "New York City, New York, United States"])))
      .toBe("San Francisco, California, United States +1");
  });

  it("puts a remote job's scope inside Remote", () => {
    expect(placeLine(job("Remote (US, Canada)", ["United States", "Canada"], "REMOTE")))
      .toBe("Remote (United States, Canada)");
  });

  it("shows text that names no place as posted", () => {
    expect(placeLine(job("Anywhere on Earth", [], "REMOTE"))).toBe("Anywhere on Earth · Remote");
  });

  it("prints a posted work style once, not beside its own label", () => {
    expect(placeLine(job("Remote", [], "REMOTE"))).toBe("Remote");
    expect(placeLine(job("On-site", [], "ONSITE"))).toBe("Onsite");
    expect(placeLine(job("Remote", [], ""))).toBe("Remote");
  });

  it("hovers every place and the text as posted", () => {
    expect(placeTitle(job("Bangalore", ["Bengaluru, Karnataka, India"])))
      .toBe("Bengaluru, Karnataka, India\nAs posted: Bangalore");
  });
});
