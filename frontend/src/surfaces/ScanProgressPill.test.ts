// Unit tests for isScanDoneEdge (observed-issue #2) — the active→done edge
// detector behind the Job Board Rescan pill's "Done" flash. The edge must fire
// exactly once when a cycle we saw run settles with jobs found, and must never
// fire on an idle-on-mount read (nothing ran) or mid-cycle.
import { describe, expect, it } from "vitest";

import { isScanDoneEdge } from "./ScanProgressPill";

describe("isScanDoneEdge", () => {
  it("does NOT fire on an idle-on-mount read (nothing ran)", () => {
    expect(
      isScanDoneEdge(false, { scan_running: false, score_pending: 0, new_found: 0 }),
    ).toBe(false);
    // Even if a prior scan left new_found > 0, a false→idle mount is not an edge.
    expect(
      isScanDoneEdge(false, { scan_running: false, score_pending: 0, new_found: 5 }),
    ).toBe(false);
  });

  it("does NOT fire while a cycle is still active", () => {
    // Scanning.
    expect(
      isScanDoneEdge(true, { scan_running: true, score_pending: 0, new_found: 0 }),
    ).toBe(false);
    // Scoring in progress.
    expect(
      isScanDoneEdge(true, { scan_running: false, score_pending: 3, new_found: 10 }),
    ).toBe(false);
  });

  it("fires on the active→idle edge when jobs were found", () => {
    expect(
      isScanDoneEdge(true, { scan_running: false, score_pending: 0, new_found: 10 }),
    ).toBe(true);
  });

  it("does NOT fire on active→idle when the scan found nothing", () => {
    expect(
      isScanDoneEdge(true, { scan_running: false, score_pending: 0, new_found: 0 }),
    ).toBe(false);
  });

  it("stays false once already idle after firing (no repeat)", () => {
    // After the edge fires, the component records wasActive=false; a subsequent
    // idle read must not re-fire.
    expect(
      isScanDoneEdge(false, { scan_running: false, score_pending: 0, new_found: 10 }),
    ).toBe(false);
  });
});
