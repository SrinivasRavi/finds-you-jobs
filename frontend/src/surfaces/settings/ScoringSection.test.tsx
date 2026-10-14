// The scoring menu: every method visible, none disabled, the prerequisite stated
// on the row that needs it (`talk.md` D44). The picker's testid and `data-on`
// live on the button, not the row, so an action below can never absorb the click.

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The i18n module is mocked so the real one's top-level await stays out of the
// test graph, the same way DegradedBootBanner.test.tsx does it. `t` returns the
// key, so an assertion on copy reads the key rather than the English string.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

afterEach(cleanup);

import type { ScoringMethod, Settings as SettingsT } from "../../api/types";
import { ScoringSection } from "./ScoringSection";

function settings(over: Partial<SettingsT> = {}): SettingsT {
  return {
    scoring_mode: "llm",
    scoring_methods: [
      { id: "llm", ready: true, needs: null },
      { id: "jev", ready: false, needs: "openrouter_key" },
      { id: "local", ready: false, needs: "model_pack" },
      { id: "keyword", ready: true, needs: null },
    ] as ScoringMethod[],
    llm_concurrency: 4,
    score_new_batch: 0,
    ...over,
  } as SettingsT;
}

describe("ScoringSection", () => {
  it("lists every method, and disables none of them", () => {
    render(<ScoringSection settings={settings()} patch={() => {}} />);
    for (const id of ["llm", "jev", "local", "keyword"]) {
      const button = screen.getByTestId(`scoring-mode-${id}`);
      expect(button).toBeTruthy();
      expect(button.hasAttribute("disabled")).toBe(false);
    }
  });

  it("marks only the selected method", () => {
    render(<ScoringSection settings={settings({ scoring_mode: "keyword" })} patch={() => {}} />);
    expect(screen.getByTestId("scoring-mode-keyword").getAttribute("data-on")).toBe("true");
    expect(screen.getByTestId("scoring-mode-llm").getAttribute("data-on")).toBe("false");
  });

  it("names what a method still needs, and offers the one action that clears it", () => {
    const onAddKey = vi.fn();
    render(<ScoringSection settings={settings()} patch={() => {}} onAddKey={onAddKey} />);
    expect(screen.getByTestId("scoring-needs-jev").textContent).toContain("needsKeyLabel");
    screen.getByTestId("scoring-add-key").click();
    expect(onAddKey).toHaveBeenCalledTimes(1);
    // The pack has no action yet, so the row says what happens instead of
    // pretending there is a button.
    expect(screen.getByTestId("scoring-needs-local").textContent).toContain("needsPackBody");
    expect(screen.queryByTestId("scoring-needs-llm")).toBeNull();
  });

  it("picks a method that is not ready, because readiness is not permission", () => {
    const patch = vi.fn();
    render(<ScoringSection settings={settings()} patch={patch} />);
    screen.getByTestId("scoring-mode-jev").click();
    expect(patch).toHaveBeenCalledWith({ scoring_mode: "jev" });
  });

  it("still renders a method the server did not report", () => {
    render(
      <ScoringSection
        settings={settings({ scoring_methods: [{ id: "llm", ready: true, needs: null }] })}
        patch={() => {}}
      />,
    );
    expect(screen.getByTestId("scoring-mode-jev")).toBeTruthy();
    expect(screen.getByTestId("scoring-mode-local")).toBeTruthy();
  });

  it("shows the batch cap only for the one method that pays per job", () => {
    const { rerender } = render(<ScoringSection settings={settings()} patch={() => {}} />);
    expect(screen.queryByTestId("score-batch-cap-25")).toBeTruthy();
    rerender(
      <ScoringSection settings={settings({ scoring_mode: "keyword" })} patch={() => {}} />,
    );
    expect(screen.queryByTestId("score-batch-cap-25")).toBeNull();
  });
});

describe("ScoringSection weights", () => {
  it("offers the 4 presets and marks the chosen one", () => {
    render(
      <ScoringSection
        settings={settings({ score_weight_preset: "skills_only" } as Partial<SettingsT>)}
        patch={() => {}}
      />,
    );
    for (const p of ["balanced", "location_first", "seniority_first", "skills_only"]) {
      expect(screen.getByTestId(`score-weight-preset-${p}`)).toBeTruthy();
    }
    expect(
      screen.getByTestId("score-weight-preset-skills_only").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("score-weight-preset-balanced").getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("keeps the sliders behind a disclosure, and the reset outside it", () => {
    // Weights are the easiest setting in this app to get lost in, so the way
    // back is never itself hidden (scoring.md 7.4).
    render(<ScoringSection settings={settings()} patch={() => {}} />);
    expect(screen.getByTestId("score-weights-reset")).toBeTruthy();
    expect(screen.queryByTestId("score-weight-core_stack")).toBeNull();
    // The disclosure is local state, so the click needs an act() boundary.
    act(() => screen.getByTestId("score-weights-disclosure").click());
    expect(screen.getByTestId("score-weight-core_stack")).toBeTruthy();
    expect(screen.getByTestId("score-location-penalty")).toBeTruthy();
  });

  it("the reset is inert on an untouched install and live once something moved", () => {
    const { unmount } = render(<ScoringSection settings={settings()} patch={() => {}} />);
    expect(screen.getByTestId("score-weights-reset").hasAttribute("disabled")).toBe(true);
    unmount();
    render(
      <ScoringSection
        settings={settings({ score_weight_preset: "location_first" } as Partial<SettingsT>)}
        patch={() => {}}
      />,
    );
    expect(screen.getByTestId("score-weights-reset").hasAttribute("disabled")).toBe(false);
  });

  it("a custom slider outranks the preset, so no chip claims to be in effect", () => {
    render(
      <ScoringSection
        settings={
          settings({
            score_weight_preset: "skills_only",
            score_weights: { core_stack: 1 },
          } as Partial<SettingsT>)
        }
        patch={() => {}}
      />,
    );
    for (const p of ["balanced", "location_first", "seniority_first", "skills_only"]) {
      expect(screen.getByTestId(`score-weight-preset-${p}`).getAttribute("aria-pressed")).toBe(
        "false",
      );
    }
  });

  it("renders against an older sidecar that sends no weight fields at all", () => {
    // A pane that throws on a missing field takes the whole Settings screen
    // down with it.
    render(<ScoringSection settings={settings()} patch={() => {}} />);
    expect(screen.getByTestId("score-weights")).toBeTruthy();
  });
});

describe("ScoringSection, what costs money", () => {
  it("shows the per-tick cap for both methods that bill per job", () => {
    // It used to show for LLM scoring alone, which was right when that was the
    // only paid method. Jev scoring bills per job too, so a first scan of
    // 1,000 jobs could run uncapped on it.
    for (const mode of ["llm", "jev"] as const) {
      const { unmount } = render(
        <ScoringSection settings={settings({ scoring_mode: mode })} patch={() => {}} />,
      );
      expect(screen.getByTestId("score-batch-cap-uncapped")).toBeTruthy();
      unmount();
    }
  });

  it("hides it for the 2 free methods, where there is nothing to cap", () => {
    for (const mode of ["keyword", "local"] as const) {
      const { unmount } = render(
        <ScoringSection settings={settings({ scoring_mode: mode })} patch={() => {}} />,
      );
      expect(screen.queryByTestId("score-batch-cap-uncapped")).toBeNull();
      unmount();
    }
  });

  it("points at the prompt pane only where a prompt exists", () => {
    const { unmount } = render(
      <ScoringSection settings={settings({ scoring_mode: "llm" })} patch={() => {}} />,
    );
    expect(screen.getByTestId("scoring-prompt-pointer")).toBeTruthy();
    unmount();
    render(<ScoringSection settings={settings({ scoring_mode: "jev" })} patch={() => {}} />);
    expect(screen.queryByTestId("scoring-prompt-pointer")).toBeNull();
  });
});

describe("ScoringSection weights start from the selected method's table (audit A10)", () => {
  const open = () => act(() => screen.getByTestId("score-weights-disclosure").click());
  const value = (axis: string) =>
    (screen.getByTestId(`score-weight-${axis}`) as HTMLInputElement).value;

  it("shows Keyword scoring's 55/30/15 on a Keyword board", () => {
    render(<ScoringSection settings={settings({ scoring_mode: "keyword" })} patch={() => {}} />);
    open();
    expect([value("core_stack"), value("years"), value("level")]).toEqual(["55", "30", "15"]);
    expect(value("hard_requirements")).toBe("0");
    expect(screen.queryByTestId("score-weights-from-jev")).toBeNull();
  });

  it("a nudge to Years changes only Years in the saved table", () => {
    const patch = vi.fn();
    render(<ScoringSection settings={settings({ scoring_mode: "keyword" })} patch={patch} />);
    open();
    const years = screen.getByTestId("score-weight-years") as HTMLInputElement;
    act(() => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      set.call(years, "35");
      years.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(patch).toHaveBeenCalledWith({
      score_weights: {
        hard_requirements: 0, core_stack: 0.55, years: 0.35, level: 0.15, nice_to_haves: 0,
      },
    });
  });

  it("says LLM scoring has no table and starts from Jev scoring's", () => {
    render(<ScoringSection settings={settings({ scoring_mode: "llm" })} patch={() => {}} />);
    open();
    expect(value("hard_requirements")).toBe("30");
    expect(screen.getByTestId("score-weights-from-jev").textContent).toBe(
      "settingsPage.scoring.weightsFromJev",
    );
  });
});
