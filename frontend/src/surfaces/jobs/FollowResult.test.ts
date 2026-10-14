// A refused follow reads in the user's language (audit A8): the sidecar sends a
// code and its facts, and the screen writes the sentence.

import type { TFunction } from "i18next";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../i18n", () => ({ default: { t: (k: string) => k } }));

import { ApiError } from "../../api/real/transport";
import { followErrorText } from "./FollowResult";

const t = ((key: string, opts?: Record<string, unknown>) =>
  opts ? `${key} ${JSON.stringify(opts)}` : key) as unknown as TFunction;

describe("followErrorText", () => {
  it("names the current Settings heading for a search-source job", () => {
    const e = new ApiError(422, "english", { code: "search_source", message: "english",
      source: "naukri", vendors: ["Greenhouse"] });
    expect(followErrorText(e, t)).toBe(
      'jobBoard.follow.errSearchSource {"source":"naukri","heading":"jobBoard.prefs.tracked.heading"}',
    );
  });

  it("lists the vendors the sidecar sent for a link no reader knows", () => {
    const e = new ApiError(422, "english", { code: "unrecognised_board", message: "english",
      vendors: ["Greenhouse", "Lever"] });
    expect(followErrorText(e, t)).toBe(
      'jobBoard.follow.errUnrecognised {"vendors":"Greenhouse, Lever"}',
    );
  });

  it("keeps any other failure's own message", () => {
    expect(followErrorText(new ApiError(500, "boom"), t)).toBe("boom");
  });
});
