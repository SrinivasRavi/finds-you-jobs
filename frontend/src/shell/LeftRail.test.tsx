// The Networking tile's visibility rule (maintainer, 2026-09-03): the tab is
// hidden while the master networking toggle is off. This reverses the earlier
// always-visible rule, so it is pinned here rather than left to the comment.
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

let session: { enabled: boolean } | undefined;
vi.mock("../api/queries", () => ({
  useLinkedInSession: () => ({ data: session }),
}));

vi.mock("../assets/logo.png", () => ({ default: "logo.png" }));

import { LeftRail } from "./LeftRail";

function renderRail(path = "/jobs") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LeftRail />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  session = undefined;
});

describe("LeftRail", () => {
  it("shows the networking tile while the toggle is on", () => {
    session = { enabled: true };
    renderRail();
    expect(screen.queryByText("nav.networking")).not.toBeNull();
  });

  it("hides the networking tile while the toggle is off", () => {
    session = { enabled: false };
    renderRail();
    expect(screen.queryByText("nav.networking")).toBeNull();
  });

  it("keeps settings reachable with the toggle off, the only way back on", () => {
    session = { enabled: false };
    renderRail();
    expect(screen.queryByText("nav.settings")).not.toBeNull();
  });

  it("shows the tile while the first read is still in flight", () => {
    // Undefined means unknown, not off. Treating it as off would blink the
    // tile out and back in on every cold start.
    session = undefined;
    renderRail();
    expect(screen.queryByText("nav.networking")).not.toBeNull();
  });
});
