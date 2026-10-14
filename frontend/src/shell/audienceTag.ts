// The one audience-tag vocabulary (D-F9); a second copy had mapped `other` to
// Peer. The kanban spells audiences out and the narrow popup row uses short
// forms, so 1 map carries 2 label slots.

import type { AudienceTag } from "../api/types";

export interface AudienceTagInfo {
  /** Spelled-out label — Networking filters. */
  labelKey: string;
  /** Short label — the referral-candidate row badge. */
  shortLabelKey: string;
  /** Badge classes (referral row). */
  cls: string;
}

const NEUTRAL = "border-border-2 bg-surface text-ink-2";

export const AUDIENCE_TAG: Record<AudienceTag, AudienceTagInfo> = {
  peer: {
    labelKey: "networking.audience.peer",
    shortLabelKey: "popups.referrals.tag.peer",
    cls: NEUTRAL,
  },
  hm: {
    labelKey: "networking.audience.hm",
    shortLabelKey: "popups.referrals.tag.hm",
    cls: "border-accent bg-accent-wash text-accent-ink",
  },
  recruiter: {
    labelKey: "networking.audience.recruiter",
    shortLabelKey: "popups.referrals.tag.recruiter",
    cls: "border-pink bg-pink-wash text-pink",
  },
  leadership: {
    labelKey: "networking.audience.leadership",
    shortLabelKey: "popups.referrals.tag.leadership",
    cls: "border-purple bg-purple-wash text-purple",
  },
  other: {
    labelKey: "networking.audience.other",
    shortLabelKey: "popups.referrals.tag.other",
    cls: NEUTRAL,
  },
};

/** A tag this build doesn't know reads as "Other" — never as a specific
 *  audience the contact was never classified into. */
export function audienceTag(tag: AudienceTag): AudienceTagInfo {
  return AUDIENCE_TAG[tag] ?? AUDIENCE_TAG.other;
}
