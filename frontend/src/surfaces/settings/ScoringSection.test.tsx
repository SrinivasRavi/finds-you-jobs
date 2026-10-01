// The scoring menu: every method visible, none disabled, the prerequisite stated
// on the row that needs it (`talk.md` D44). The picker's testid and `data-on`
// live on the button, not the row, so an action below can never absorb the click.

import { cleanup, render, screen } from "@testing-library/react";
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
