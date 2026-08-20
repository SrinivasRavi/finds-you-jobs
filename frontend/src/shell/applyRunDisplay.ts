// The one Apply-Run status → display table (duplication audit D-F14). The
// tracker card's slot map and the Applier panel's phase pill both classified the
// same enum, and their fallbacks were opposites: the card degraded an unknown
// status to the grey "Apply" slot, while the panel fell through to a GREEN
// "Completed" — an unrecognised status reading as a submitted application.
//
// One table now carries both vocabularies (the surfaces word the same status
// differently on purpose: "Ready for review" in the panel, "Review & submit" on
// the card), and the single fallback is the conservative one.

import type { ApplyRunStatus } from "../api/types";

/** Applier-panel pill tone. */
export type ApplyTone = "info" | "ok" | "warn" | "bad";

export interface ApplyRunDisplay {
  tone: ApplyTone;
  /** Panel shows a spinner only while the run is genuinely live. */
  live: boolean;
  /** Applier-panel phase label (i18n key). */
  phaseKey: string;
  /** Tracker-card Apply slot label (i18n key). */
  slotKey: string;
  /** Tracker-card PacketSlotTag state key. */
  slotState: string;
}

const TABLE: Record<ApplyRunStatus, ApplyRunDisplay> = {
  none: {
    tone: "info",
    live: false,
    phaseKey: "popups.applier.phase.notStarted",
    slotKey: "tracker.applySlot.apply",
    slotState: "none",
  },
  queued: {
    tone: "info",
    live: true,
    phaseKey: "popups.applier.phase.queued",
    slotKey: "tracker.applySlot.applying",
    slotState: "generating",
  },
  waiting_for_packet: {
    tone: "info",
    live: true,
    phaseKey: "popups.applier.phase.waitingForPacket",
    slotKey: "tracker.applySlot.applying",
    slotState: "generating",
  },
  running: {
    // The panel refines this one further from the run's free-text phase.
    tone: "info",
    live: true,
    phaseKey: "popups.applier.phase.working",
    slotKey: "tracker.applySlot.applying",
    slotState: "generating",
  },
  ready_for_human: {
    tone: "warn",
    live: false,
    phaseKey: "popups.applier.phase.readyForHuman",
    slotKey: "tracker.applySlot.review",
    slotState: "pending",
  },
  submitted: {
    tone: "ok",
    live: false,
    phaseKey: "popups.applier.phase.submitted",
    slotKey: "tracker.applySlot.submitted",
    slotState: "approved",
  },
  blocked: {
    tone: "bad",
    live: false,
    phaseKey: "popups.applier.phase.blocked",
    slotKey: "tracker.applySlot.retry",
    slotState: "failed",
  },
  timed_out: {
    tone: "bad",
    live: false,
    phaseKey: "popups.applier.phase.timedOut",
    slotKey: "tracker.applySlot.retry",
    slotState: "failed",
  },
  interrupted: {
    tone: "bad",
    live: false,
    phaseKey: "popups.applier.phase.interrupted",
    slotKey: "tracker.applySlot.retry",
    slotState: "failed",
  },
  failed: {
    tone: "bad",
    live: false,
    phaseKey: "popups.applier.phase.failed",
    slotKey: "tracker.applySlot.retry",
    slotState: "failed",
  },
};

/** A status this build doesn't know degrades to the inert "no run" display —
 *  never to a success state. */
export function applyRunDisplay(status: ApplyRunStatus): ApplyRunDisplay {
  return TABLE[status] ?? TABLE.none;
}
