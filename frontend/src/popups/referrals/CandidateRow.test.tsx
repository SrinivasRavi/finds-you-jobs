// The candidate row's right-hand slot holds its shape (maintainer,
// 2026-08-16): an already-reached row shows a dull, non-clickable "Requested"
// box in the exact footprint of the Connect/Message button, so the LinkedIn
// button beside it never shifts between rows. A mid-send row keeps the
// (disabled) action button for the same reason.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReferralCandidate } from "../../api/types";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
  initReactI18next: { type: "3rdParty", init: () => {} },
}));

import { CandidateRow } from "./CandidateRow";

function candidate(overrides: Partial<ReferralCandidate>): ReferralCandidate {
  return {
    contact_id: "c1",
    name: "Sarah Tan",
    role: "Engineering Manager",
    company: "Northline",
    linkedin_url: "https://www.linkedin.com/in/sarah-tan",
    degree: null,
    audience_tag: "hm",
    channel: "connection_note",
    draft: "Hi Sarah",
    already_reached: false,
    ...overrides,
  } as ReferralCandidate;
}

const noop = () => undefined;

function renderRow(c: ReferralCandidate, opts?: { sendable?: boolean; sending?: boolean }) {
  return render(
    <CandidateRow
      c={c}
      connected
      sendable={opts?.sendable ?? true}
      draft={c.draft ?? ""}
      expanded={false}
      sending={opts?.sending ?? false}
      failure={null}
      onAsk={noop}
      onExpand={noop}
      onDraft={noop}
      onOpenLinkedIn={noop}
    />,
  );
}

afterEach(cleanup);

describe("CandidateRow right-hand slot", () => {
  it("a sendable row shows the Connect button at the fixed slot width", () => {
    renderRow(candidate({}));
    const send = screen.getByTestId("referrals-row-send");
    expect(send.tagName).toBe("BUTTON");
    expect(send.textContent).toBe("popups.referrals.rowConnect");
    expect(send.className).toContain("w-[80px]");
    expect(screen.queryByTestId("referrals-row-requested")).toBeNull();
  });

  it("an already-reached row swaps Connect for a dull non-clickable Requested box, same footprint", () => {
    renderRow(candidate({ already_reached: true }), { sendable: false });
    expect(screen.queryByTestId("referrals-row-send")).toBeNull();
    const requested = screen.getByTestId("referrals-row-requested");
    expect(requested.tagName).toBe("SPAN"); // not a button — nothing to click
    expect(requested.textContent).toBe("popups.referrals.rowRequested");
    expect(requested.className).toContain("w-[80px]");
    // The LinkedIn button keeps its place beside the filled slot.
    expect(screen.getByTestId("referrals-row-linkedin")).toBeTruthy();
  });

  it("a reached DM row reads Messaged, not Requested", () => {
    renderRow(candidate({ already_reached: true, channel: "dm" }), { sendable: false });
    expect(screen.getByTestId("referrals-row-requested").textContent).toBe(
      "popups.referrals.rowMessaged",
    );
  });

  it("a mid-send row keeps the (disabled) action button — the slot never empties", () => {
    renderRow(candidate({}), { sendable: false, sending: true });
    const send = screen.getByTestId("referrals-row-send") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(send.className).toContain("w-[80px]");
  });
});
