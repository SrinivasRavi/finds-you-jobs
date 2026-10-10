// The one LinkedIn session status → pill mapping (duplication audit D-F8).
// Settings and the Networking header each had their own switch and they had
// already drifted: Settings named `expired` explicitly, while Networking folded
// it into the generic "Connect LinkedIn" chip — a user whose cookie had lapsed
// was told to connect rather than told the session expired.
//
// Only the status→semantics decision is shared. Each surface keeps its own
// chrome and copy: the header pill is 22px with a currentColor dot, Settings'
// is 20px with an explicit hex dot, and the two use different wording.

import type { LinkedInSessionState } from "../api/types";

export type LinkedInPillState =
  | "connected"
  | "connecting"
  | "backingOff"
  | "expired"
  | "disconnected";

export type LinkedInPillTone = "good" | "warn" | "bad";

const PILL: Record<
  LinkedInSessionState["status"],
  { state: LinkedInPillState; tone: LinkedInPillTone }
> = {
  valid: { state: "connected", tone: "good" },
  connecting: { state: "connecting", tone: "warn" },
  backing_off: { state: "backingOff", tone: "bad" },
  expired: { state: "expired", tone: "bad" },
  never_set: { state: "disconnected", tone: "bad" },
};

/** A status this build doesn't know degrades to "disconnected" — never to a
 *  healthy-looking chip, and never silently onto another named state. */
/** Chrome and copy for the 22px chip. Networking's header and the browser
 *  modal's header render the SAME chip from the same keys, so they share this;
 *  Settings' 20px chip keeps its own (it uses the -2 border shades and its own
 *  wording). Before this they were 2 byte-identical copies whose comments each
 *  claimed the split was deliberate. */
export const HEADER_PILL_CLS: Record<LinkedInPillTone, string> = {
  good: "bg-good-wash border-good text-good",
  warn: "bg-warn-wash border-warn text-warn",
  bad: "bg-bad-wash border-bad text-bad",
};

export const HEADER_PILL_LABEL: Record<LinkedInPillState, string> = {
  connected: "networking.linkedinPill.connected",
  connecting: "networking.linkedinPill.connecting",
  backingOff: "networking.linkedinPill.backingOff",
  expired: "networking.linkedinPill.expired",
  disconnected: "networking.linkedinPill.connect",
};

export function linkedInStatusPill(status: LinkedInSessionState["status"]): {
  state: LinkedInPillState;
  tone: LinkedInPillTone;
} {
  return PILL[status] ?? PILL.never_set;
}
