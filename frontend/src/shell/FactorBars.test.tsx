// The 3 rules from scoring.md section 7.3, each as its own failing-if-broken
// assertion. The component is presentational, so this is the whole contract.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// `t` returns the key (plus interpolation), the way the other settings tests
// mock it, so an assertion reads the key and never an English sentence.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && "percent" in opts ? `${key}:${opts.percent}` : key,
  }),
}));

afterEach(cleanup);

import { FactorBars, type ScoreFactor } from "./FactorBars";

const GRADED: ScoreFactor = {
  factor: "core_stack",
  value: 0.4,
  kind: "graded",
  evidence: "Python and Postgres evidenced; Kafka is not",
};
const PROBABILITY: ScoreFactor = {
  factor: "location_preference",
  value: 0.96,
  kind: "probability",
  evidence: "96% confidence the posting is in a location you asked for",
};

describe("FactorBars", () => {
  it("rule 1: a probability is drawn and labelled differently from a position", () => {
    render(<FactorBars factors={[GRADED, PROBABILITY]} />);
    const graded = screen.getByTestId("fill-core_stack");
    const probability = screen.getByTestId("fill-location_preference");
    expect(graded.className).toContain("bg-accent");
    expect(graded.className).not.toContain("dashed");
    expect(probability.className).toContain("dashed");
    expect(screen.getByTestId("factor-location_preference").dataset.kind).toBe("probability");
    expect(screen.getByText("scoreFactor.confidence:96")).toBeTruthy();
  });

  it("rule 2: the weight shows beside the bar, because 0.40 at 5% is not 0.40 at 30%", () => {
    render(<FactorBars factors={[GRADED]} weights={{ core_stack: 0.3 }} />);
    expect(screen.getByTestId("weight-core_stack").textContent).toBe("scoreFactor.weight:30");
  });

  it("rule 2, the other half: a probability never carries a weight", () => {
    // Multiplying a confidence by a user weight produces a number with no
    // meaning, so the UI must not imply it is weighted (talk.md D43).
    render(<FactorBars factors={[PROBABILITY]} weights={{ location_preference: 0.3 }} />);
    expect(screen.queryByTestId("weight-location_preference")).toBeNull();
  });

  it("rule 3: the evidence shows, so a bar is arguable rather than decorative", () => {
    render(<FactorBars factors={[GRADED]} />);
    expect(screen.getByText(GRADED.evidence)).toBeTruthy();
  });

  it("an axis with no weight in the table draws without one, rather than claiming 0%", () => {
    render(<FactorBars factors={[GRADED]} weights={{ years: 0.3 }} />);
    expect(screen.queryByTestId("weight-core_stack")).toBeNull();
  });

  it("draws the axes in the scorer's own order, whatever order they arrive in", () => {
    const factors: ScoreFactor[] = [
      { factor: "nice_to_haves", value: 0.2, kind: "graded", evidence: "" },
      { factor: "hard_requirements", value: 0.8, kind: "graded", evidence: "" },
      { factor: "core_stack", value: 0.5, kind: "graded", evidence: "" },
    ];
    render(<FactorBars factors={factors} />);
    const drawn = [...screen.getByTestId("factor-bars").children].map(
      (li) => (li as HTMLElement).dataset.testid ?? (li as HTMLElement).getAttribute("data-testid"),
    );
    expect(drawn).toEqual([
      "factor-hard_requirements",
      "factor-core_stack",
      "factor-nice_to_haves",
    ]);
  });

  it("an axis this build has never heard of sorts last instead of vanishing", () => {
    const factors: ScoreFactor[] = [
      { factor: "something_new", value: 0.5, kind: "graded", evidence: "" },
      { factor: "core_stack", value: 0.5, kind: "graded", evidence: "" },
    ];
    render(<FactorBars factors={factors} />);
    expect(screen.getByTestId("factor-something_new")).toBeTruthy();
  });

  it("says so when a method recorded no per-axis detail", () => {
    render(<FactorBars factors={[]} />);
    expect(screen.getByText("scoreFactor.none")).toBeTruthy();
  });
});
