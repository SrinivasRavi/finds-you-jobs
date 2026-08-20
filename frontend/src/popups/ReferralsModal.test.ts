// Unit tests for the Sending-state reconcile (2026-08-16): the popup's
// per-row Sending badges follow the SERVER's in-flight send ops on every
// candidates refresh. This is what makes the badge survive a close/reopen
// (the op is still queued/running server-side) AND what un-wedges a row whose
// terminal SSE event was missed (a reconnect gap — or dev HMR — swallowed it:
// the live 2026-08-16 stuck "Sending" row). Just-clicked rows get a grace
// window, because a refetch racing the click's 202 would otherwise strip the
// fresh badge.

import { describe, expect, it } from "vitest";

import { reconcileSendingIds, SEND_CLICK_GRACE_MS } from "./ReferralsModal";

const NOW = 1_000_000;

describe("reconcileSendingIds", () => {
  it("adopts the server's in-flight list (the reopen-seeding case)", () => {
    const next = reconcileSendingIds(new Set(), ["c1", "c2"], new Map(), NOW);
    expect([...next].sort()).toEqual(["c1", "c2"]);
  });

  it("drops a row the server no longer lists — a missed terminal event can't wedge it", () => {
    const clicked = new Map([["c1", NOW - SEND_CLICK_GRACE_MS - 1]]);
    const next = reconcileSendingIds(new Set(["c1"]), [], clicked, NOW);
    expect(next.size).toBe(0);
  });

  it("keeps a just-clicked row through a refetch that predates its 202", () => {
    const clicked = new Map([["c1", NOW - 1_000]]);
    const next = reconcileSendingIds(new Set(["c1"]), [], clicked, NOW);
    expect([...next]).toEqual(["c1"]);
  });

  it("a row never clicked locally follows the server alone", () => {
    const next = reconcileSendingIds(new Set(["ghost"]), ["real"], new Map(), NOW);
    expect([...next]).toEqual(["real"]);
  });

  it("returns the same set instance when nothing changed (no render churn)", () => {
    const prev = new Set(["c1"]);
    const next = reconcileSendingIds(prev, ["c1"], new Map(), NOW);
    expect(next).toBe(prev);
  });
});
