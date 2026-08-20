// Unit tests for the two mapper invariants real.ts used to enforce by comment
// alone (D-F15): the cost invariant behind every ledger/operation row, and the
// enqueue/retry/cancel stub's refusal to invent what it doesn't know.
import { describe, expect, it } from "vitest";

import { refusalOf, stubOperation, usdOrNull } from "./real";

describe("usdOrNull — a real paid call must never read as verified-free", () => {
  it("keeps an unknown cost null instead of collapsing it to 0", () => {
    expect(usdOrNull(null)).toBeNull();
    expect(usdOrNull(undefined)).toBeNull();
    // A non-numeric wire value (an unpriced model can send "" / "unknown") is
    // still unknown, never free.
    expect(usdOrNull("")).toBeNull();
    expect(usdOrNull("0.02")).toBeNull();
  });

  it("passes a real number through, including a genuine zero", () => {
    expect(usdOrNull(0.0123)).toBe(0.0123);
    // 0 from the sidecar means a measured free call — that one IS honest.
    expect(usdOrNull(0)).toBe(0);
  });
});

describe("refusalOf — an in-band cap refusal must not read as a silent success", () => {
  it("extracts the verbatim reason from a cap_or_backoff result_ref", () => {
    expect(
      refusalOf({
        sent: false,
        error: "cap_or_backoff",
        reason: "note budget exhausted — the free-plan allowance is out",
      }),
    ).toBe("note budget exhausted — the free-plan allowance is out");
  });

  it("falls back to the code when the reason is missing, never to null", () => {
    expect(refusalOf({ error: "cap_or_backoff", reason: "" })).toBe("cap_or_backoff");
  });

  it("stays null for real successes and other failures", () => {
    expect(refusalOf(null)).toBeNull();
    expect(refusalOf({ sent: true, error: "", reason: "" })).toBeNull();
    expect(refusalOf({ error: "rate_limited", reason: "backing off" })).toBeNull();
  });
});

describe("stubOperation", () => {
  it("reports the unknown fields as unknown, never as measured values", () => {
    const op = stubOperation({ id: "op-1", kind: "scan", state: "queued" });
    expect(op).toMatchObject({
      id: "op-1",
      kind: "scan",
      state: "queued",
      progress: 0,
      step: "queued",
      usage: null,
      error: null,
    });
    expect(op.created_at).not.toBe("");
  });
});
