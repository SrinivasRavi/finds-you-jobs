// Unit tests for the Sending-state reconcile: per-row Sending badges follow the
// server's in-flight send ops on every refresh, so a badge survives close and
// reopen and a row whose terminal event was missed can't stay stuck. Fresh clicks
// get a grace window so a refetch racing the 202 doesn't strip the badge.

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
