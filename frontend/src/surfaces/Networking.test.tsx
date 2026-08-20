// Integration tests for the watchable single-click send (maintainer,
// 2026-08-15): a Send from the contact composer fires the ONE reach-out path
// with no second confirm dialog, closes the composer, and opens the LinkedIn
// browser modal (app-level since 2026-08-16) so the send is watched from its
// first step. Also covers the header's LinkedIn status button (opens the
// modal / lands on Settings / pulses while an op runs), the card/modal
// last-message attribution ("Me:" vs the contact's first name), and that the
// removed "Reach out by URL" entry stays gone. The queries module and the
// browser-modal provider are mocked; the composer, the modals, and the
// navigation are the real code (MemoryRouter).

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NetContact } from "../api/types";

const h = vi.hoisted(() => ({
  reachOutMutate: vi.fn(),
  addMutateAsync: vi.fn(),
  updateMutate: vi.fn(),
  syncMutate: vi.fn(),
  openLinkedIn: vi.fn(),
  viewInBrowser: vi.fn((..._args: unknown[]) => Promise.resolve()),
  syncInFlight: false,
  contacts: [] as unknown[],
  session: { enabled: true, status: "expired" } as Record<string, unknown>,
  browserOps: { current: null, queued: [], settled: [] } as {
    current: unknown;
    queued: unknown[];
    settled: unknown[];
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && "count" in opts
        ? `${key}(${String(opts.count)})`
        : opts && "name" in opts
          ? `${key}(${String(opts.name)})`
          : key,
  }),
  // Networking → jobFormat → ../i18n boots the real i18next instance, which
  // plugs this in; a no-op third-party module keeps that boot inert here.
  initReactI18next: { type: "3rdParty", init: () => {} },
}));
vi.mock("../api/queries", () => ({
  useContacts: () => ({ data: h.contacts }),
  useArchivedContacts: () => ({ data: [] }),
  useLinkedInSession: () => ({ data: h.session }),
  useReachOut: () => ({ mutate: h.reachOutMutate, isPending: false }),
  useAddContact: () => ({ mutateAsync: h.addMutateAsync, isPending: false }),
  useUpdateContact: () => ({ mutate: h.updateMutate }),
  useSyncContacts: () => ({ mutate: h.syncMutate, isPending: false }),
  useContactSyncInFlight: () => h.syncInFlight,
  useViewInBrowser: () => ({ mutate: h.viewInBrowser, isPending: false }),
}));
// The heavy surface pieces are stand-ins: what's under test is that a send
// brings them into view, not what they paint.
vi.mock("./BrowserSurface", () => ({
  BrowserSurface: (props: { surface?: string; origin?: string; autoHome?: boolean }) => (
    <div
      data-testid="fake-browser-surface"
      data-surface={props.surface}
      data-origin={props.origin}
      data-auto-home={String(props.autoHome)}
    />
  ),
}));
vi.mock("./BrowserOpPlan", () => ({
  useBrowserOps: () => ({ current: null, queued: [], settled: [] }),
  // Mirror the real semantics (non-terminal current op = busy) so the pill's
  // in-progress state is drivable from `h.browserOps`.
  opBusy: (op: { state?: string } | null) =>
    op != null && !["succeeded", "failed", "cancelled"].includes(op.state ?? ""),
  BrowserOpPlan: () => <aside data-testid="fake-op-plan" />,
}));
// The app-level browser-modal provider: Networking only needs the opener and
// the op feed; the modal itself has its own test file.
vi.mock("./LinkedInBrowserProvider", () => ({
  useLinkedInBrowser: () => ({
    open: h.openLinkedIn,
    close: () => undefined,
    isOpen: false,
    ops: h.browserOps,
  }),
}));
vi.mock("../shell/MasterResumeLauncher", () => ({
  MasterResumeLauncher: () => null,
}));

import {
  cardActivityAt,
  lastMessageAttribution,
  Networking,
  sortColumn,
  syncedAgo,
  syncStoppedKey,
} from "./Networking";

// Networking navigates (the status button's expired/never-connected states
// land on Settings), so it renders inside a MemoryRouter with a probe route
// standing in for the Settings surface.
function renderNetworking() {
  return render(
    <MemoryRouter initialEntries={["/networking"]}>
      <Routes>
        <Route path="/networking" element={<Networking />} />
        <Route path="/settings" element={<div data-testid="settings-probe" />} />
      </Routes>
    </MemoryRouter>,
  );
}

function contact(overrides: Partial<NetContact>): NetContact {
  return {
    id: "c1",
    linkedin_url: "https://www.linkedin.com/in/sarah-tan",
    name: "Sarah Tan",
    current_role: "Engineering Manager",
    current_company: "Northline",
    headline: "",
    connection_degree: 1,
    is_first_degree: true,
    audience_tag: "hm",
    warmth: "warm",
    connection_status: "accepted",
    last_message: null,
    last_message_at: null,
    last_message_direction: null,
    last_message_from: null,
    sent_at: null,
    accepted_at: null,
    added_at: "2026-08-01T00:00:00+00:00",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  h.contacts = [];
  h.syncInFlight = false;
  h.session = { enabled: true, status: "expired" };
  h.browserOps = { current: null, queued: [], settled: [] };
});

describe("Networking watchable single-click send", () => {
  it("composer Send: one click fires the send, no second dialog, and the browser modal opens", async () => {
    h.contacts = [contact({})];
    renderNetworking();

    // Open the contact modal from its kanban card.
    fireEvent.click(screen.getByTestId("contact-card"));
    const box = screen.getByTestId("contact-compose-message") as HTMLTextAreaElement;
    expect(box.value.length).toBeGreaterThan(0); // stage template prefilled

    // The irreversibility + channel line sits beside the single Send button.
    expect(screen.getByTestId("contact-compose-channel").textContent).toContain(
      "networking.compose.channelDm",
    );

    fireEvent.change(box, { target: { value: "Hi Sarah, my own words." } });
    fireEvent.click(screen.getByTestId("contact-compose-send"));

    // ONE click: the send fired with the exact message — no confirm overlay.
    expect(h.reachOutMutate).toHaveBeenCalledTimes(1);
    expect(h.reachOutMutate.mock.calls[0]![0]).toEqual({
      contacts: [{ contact_id: "c1", message: "Hi Sarah, my own words." }],
    });
    expect(screen.queryByTestId("reach-out-confirm")).toBeNull();

    // The browser modal opened so the send is watched live; the composer
    // modal is gone with it.
    expect(h.openLinkedIn).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("contact-compose-message")).toBeNull();
  });

  it("the contact modal's LinkedIn link enqueues a queued page-view and opens the browser modal", () => {
    // The view is a view_page OPERATION (2026-08-16): it waits its turn on
    // the lane instead of navigating over a running op. Through a MUTATION,
    // so a failed enqueue lands in the global error banner, never silence.
    h.contacts = [contact({})];
    renderNetworking();
    fireEvent.click(screen.getByTestId("contact-card"));
    fireEvent.click(screen.getByTestId("contact-open-linkedin"));

    expect(h.viewInBrowser).toHaveBeenCalledTimes(1);
    expect(h.viewInBrowser.mock.calls[0]).toEqual([
      {
        url: "https://www.linkedin.com/in/sarah-tan",
        surface: "linkedin",
        contactId: "c1",
      },
    ]);
    expect(h.openLinkedIn).toHaveBeenCalledTimes(1);
    // The contact modal closed with the handoff to the browser modal.
    expect(screen.queryByTestId("contact-open-linkedin")).toBeNull();
  });

  it("the reach-out-by-url entry is gone; Add a contact by URL remains", () => {
    // Maintainer decision, 2026-08-15: the one-step paste-a-URL send
    // duplicated "Add a contact by URL" + the contact modal's composer.
    renderNetworking();
    expect(screen.queryByTestId("reach-out-by-url-button")).toBeNull();
    expect(screen.getByTestId("add-contact-by-url-button")).toBeTruthy();
  });

});

// The header's LinkedIn status button (2026-08-16, was a read-only pill): one
// FIXED footprint across every state, opening the browser modal — except the
// expired/never-connected states, which land on Settings (the connect flow
// lives there) — and pulsing "in progress" while an op drives the surface.
describe("LinkedIn status button", () => {
  it("connected: opens the browser modal, never navigates", () => {
    h.session = { enabled: true, status: "valid" };
    renderNetworking();
    const btn = screen.getByTestId("linkedin-state-pill");
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.textContent).toBe("networking.linkedinPill.connected");
    expect(btn.getAttribute("title")).toBe("networking.linkedinPill.titleOpen");
    fireEvent.click(btn);
    expect(h.openLinkedIn).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("settings-probe")).toBeNull();
  });

  it("expired and never-connected: lands on Settings, never opens the modal", () => {
    for (const status of ["expired", "never_set"]) {
      h.session = { enabled: true, status };
      renderNetworking();
      const btn = screen.getByTestId("linkedin-state-pill");
      expect(btn.getAttribute("title")).toBe("networking.linkedinPill.titleSettings");
      fireEvent.click(btn);
      expect(screen.getByTestId("settings-probe")).toBeTruthy();
      expect(h.openLinkedIn).not.toHaveBeenCalled();
      cleanup();
    }
  });

  it("a live op wins the button's face: pulse + in-progress label, opens the modal", () => {
    h.session = { enabled: true, status: "valid" };
    h.browserOps = {
      current: { id: "op1", kind: "send", state: "running" },
      queued: [],
      settled: [],
    };
    renderNetworking();
    const btn = screen.getByTestId("linkedin-state-pill");
    expect(btn.textContent).toBe("networking.linkedinPill.inProgress");
    expect(btn.querySelector(".animate-pulse")).toBeTruthy();
    fireEvent.click(btn);
    expect(h.openLinkedIn).toHaveBeenCalledTimes(1);
  });

  it("queued-only work also reads in progress (the lane is claimed)", () => {
    h.session = { enabled: true, status: "valid" };
    h.browserOps = {
      current: null,
      queued: [{ id: "op2", kind: "view_page" }],
      settled: [],
    };
    renderNetworking();
    expect(screen.getByTestId("linkedin-state-pill").textContent).toBe(
      "networking.linkedinPill.inProgress",
    );
  });

  it("keeps one fixed footprint across states (no header shift)", () => {
    h.session = { enabled: true, status: "valid" };
    renderNetworking();
    const cls = screen.getByTestId("linkedin-state-pill").className;
    expect(cls).toContain("h-[30px]");
    expect(cls).toContain("w-[170px]");
  });

  it("hidden entirely while Referral Outreach is off", () => {
    h.session = { enabled: false, status: "valid" };
    renderNetworking();
    expect(screen.queryByTestId("linkedin-state-pill")).toBeNull();
  });
});

// Manual-only sync (maintainer decision, 2026-08-15): mounting the surface
// fires NOTHING — the Sync button is the one trigger — and the busy state
// follows the real in-flight contact_sync op, not just the brief POST.
describe("manual-only sync", () => {
  it("fires no sync on mount; the button fires exactly one", () => {
    h.session = {
      enabled: true,
      status: "valid",
      contact_sync_last_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    };
    renderNetworking();
    // Opening the surface syncs nothing — the on-open auto-sync is gone.
    expect(h.syncMutate).not.toHaveBeenCalled();
    const stamp = screen.getByTestId("last-synced-stamp");
    expect(stamp.textContent).toBe("networking.sync.lastSynced");
    expect(stamp.getAttribute("title")).toBe("networking.sync.title");
    const btn = screen.getByTestId("sync-contacts-btn");
    expect(btn.getAttribute("title")).toBe("networking.sync.title");
    fireEvent.click(btn);
    expect(h.syncMutate).toHaveBeenCalledTimes(1);
  });

  it("the busy state holds for the life of the in-flight op, not just the POST", () => {
    h.session = { enabled: true, status: "valid", contact_sync_last_at: null };
    h.syncInFlight = true; // a live contact_sync op (isPending stays false)
    renderNetworking();
    const btn = screen.getByTestId("sync-contacts-btn") as HTMLButtonElement;
    // In-progress = disabled + the spinning icon + the running note INSIDE
    // the merged button (2026-08-16) — never a stamp or a stopped note.
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain("networking.sync.label");
    expect(btn.textContent).toContain("networking.sync.running");
    expect(btn.querySelector("svg.animate-spin")).toBeTruthy();
    expect(screen.queryByTestId("last-synced-stamp")).toBeNull();
    expect(screen.queryByTestId("sync-stopped-note")).toBeNull();
  });

  it("omits the stamp before any successful sync; the Sync button remains", () => {
    h.session = { enabled: true, status: "valid", contact_sync_last_at: null };
    renderNetworking();
    expect(screen.queryByTestId("last-synced-stamp")).toBeNull();
    const btn = screen.getByTestId("sync-contacts-btn") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toBe("networking.sync.label");
  });

  it("keeps one fixed footprint across states (no header shift)", () => {
    h.session = { enabled: true, status: "valid", contact_sync_last_at: null };
    renderNetworking();
    const cls = screen.getByTestId("sync-contacts-btn").className;
    expect(cls).toContain("h-[30px]");
    expect(cls).toContain("w-[240px]");
  });

  it("shows neither control when the feature is not usable", () => {
    h.session = { enabled: false, status: "valid" };
    renderNetworking();
    expect(screen.queryByTestId("last-synced-stamp")).toBeNull();
    expect(screen.queryByTestId("sync-contacts-btn")).toBeNull();
    expect(h.syncMutate).not.toHaveBeenCalled();
  });
});

// The card and the modal show the thread's REAL last message with honest
// attribution (maintainer, 2026-08-15): "Me:" when we sent last, the contact's
// first name when they did. The name is data; the label goes through i18n
// (mocked here as `key(name)`).
describe("last-message attribution", () => {
  it("an incoming message is attributed to the sender's first name on card and modal", () => {
    h.contacts = [contact({
      last_message: "Happy to refer you, send me the link!",
      last_message_at: new Date().toISOString(),
      last_message_direction: "them",
      last_message_from: "Sarah Tan",
    })];
    renderNetworking();
    const card = screen.getByTestId("contact-last-message");
    expect(card.textContent).toContain("networking.card.from(Sarah)");
    expect(card.textContent).toContain("Happy to refer you, send me the link!");
    // The modal attributes the same way — no bare "Last message" label.
    fireEvent.click(screen.getByTestId("contact-card"));
    const modal = screen.getByTestId("contact-modal-last-message");
    expect(modal.textContent).toContain("networking.card.from(Sarah)");
    expect(modal.textContent).toContain("Happy to refer you, send me the link!");
  });

  it("our own last message reads Me: on card and modal", () => {
    h.contacts = [contact({
      last_message: "Hi Sarah, following up on my note.",
      last_message_at: new Date().toISOString(),
      last_message_direction: "me",
    })];
    renderNetworking();
    expect(screen.getByTestId("contact-last-message").textContent).toContain(
      "networking.card.me",
    );
    fireEvent.click(screen.getByTestId("contact-card"));
    expect(screen.getByTestId("contact-modal-last-message").textContent).toContain(
      "networking.card.me",
    );
  });

  it("falls back through from_name → contact name, and to Me: without a direction", () => {
    // No thread from_name → the stored contact name's first token.
    expect(
      lastMessageAttribution({
        last_message_direction: "them", last_message_from: null, name: "Grace Hopper",
      }),
    ).toEqual({ key: "networking.card.from", name: "Grace" });
    // A single-token name is used whole.
    expect(
      lastMessageAttribution({
        last_message_direction: "them", last_message_from: "Cher", name: "",
      }),
    ).toEqual({ key: "networking.card.from", name: "Cher" });
    // A row with no name anywhere (broken data) degrades to the Me: label
    // rather than rendering a bare ":" prefix.
    expect(
      lastMessageAttribution({
        last_message_direction: "them", last_message_from: null, name: "",
      }),
    ).toEqual({ key: "networking.card.me" });
    // The OutreachLog fallback (direction "me") and legacy rows (null) read Me:.
    expect(
      lastMessageAttribution({
        last_message_direction: "me", last_message_from: null, name: "Grace Hopper",
      }),
    ).toEqual({ key: "networking.card.me" });
    expect(
      lastMessageAttribution({
        last_message_direction: null, last_message_from: null, name: "Grace Hopper",
      }),
    ).toEqual({ key: "networking.card.me" });
  });
});

describe("syncedAgo buckets", () => {
  const minsAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

  it("null without a stamp; just-now under a minute", () => {
    expect(syncedAgo(null)).toBeNull();
    expect(syncedAgo(minsAgo(0))).toEqual({ key: "networking.sync.justNow" });
  });

  it("minutes, hours, then days", () => {
    expect(syncedAgo(minsAgo(12))).toEqual({ key: "networking.sync.minutesAgo", n: 12 });
    expect(syncedAgo(minsAgo(3 * 60))).toEqual({ key: "networking.sync.hoursAgo", n: 3 });
    expect(syncedAgo(minsAgo(2 * 24 * 60))).toEqual({ key: "networking.sync.daysAgo", n: 2 });
  });

  it("garbage timestamps render nothing rather than lying", () => {
    expect(syncedAgo("not-a-date")).toBeNull();
  });
});

// Column ordering (maintainer ask, 2026-08-16): each column shows its most
// recently active card first — the timestamp the card itself displays — and
// NEVER orders on last_touched_at, the sync engine's rotation cursor, whose
// churn made the board reshuffle on every Sync press.
describe("column ordering", () => {
  it("cardActivityAt prefers the displayed date and falls back through the lifecycle stamps", () => {
    const base = {
      last_message_at: null, sent_at: null, accepted_at: null,
      added_at: "2026-08-01T00:00:00+00:00",
    };
    expect(cardActivityAt({ ...base, last_message_at: "2026-08-15T10:00:00+00:00" }))
      .toBe(Date.parse("2026-08-15T10:00:00+00:00"));
    expect(cardActivityAt({ ...base, sent_at: "2026-08-10T00:00:00+00:00" }))
      .toBe(Date.parse("2026-08-10T00:00:00+00:00"));
    expect(cardActivityAt({ ...base, accepted_at: "2026-08-05T00:00:00+00:00" }))
      .toBe(Date.parse("2026-08-05T00:00:00+00:00"));
    expect(cardActivityAt(base)).toBe(Date.parse("2026-08-01T00:00:00+00:00"));
    // A garbage displayed date falls through to the next real stamp.
    expect(cardActivityAt({ ...base, last_message_at: "not-a-date" }))
      .toBe(Date.parse("2026-08-01T00:00:00+00:00"));
  });

  it("sortColumn orders most-recent first with a stable name/id tiebreak", () => {
    const older = contact({
      id: "c-older", name: "Alpha Early",
      last_message_at: "2026-08-10T09:00:00+00:00",
    });
    const newest = contact({
      id: "c-newest", name: "Zoe Late",
      last_message_at: "2026-08-15T22:00:00+00:00",
    });
    const tiedA = contact({
      id: "c-tied-a", name: "Ann Tied",
      last_message_at: "2026-08-12T12:00:00+00:00",
    });
    const tiedB = contact({
      id: "c-tied-b", name: "Bob Tied",
      last_message_at: "2026-08-12T12:00:00+00:00",
    });
    const sorted = sortColumn([tiedB, older, newest, tiedA]);
    expect(sorted.map((c) => c.id)).toEqual(["c-newest", "c-tied-a", "c-tied-b", "c-older"]);
    // Stable under any input permutation (the anti-shuffle property).
    expect(sortColumn([older, tiedA, tiedB, newest]).map((c) => c.id))
      .toEqual(["c-newest", "c-tied-a", "c-tied-b", "c-older"]);
  });

  it("renders a column's cards most-recent first", () => {
    h.contacts = [
      contact({ id: "c-old", name: "Old Card",
                last_message_at: "2026-08-01T00:00:00+00:00" }),
      contact({ id: "c-new", name: "New Card",
                last_message_at: "2026-08-15T00:00:00+00:00" }),
    ];
    renderNetworking();
    const ids = screen.getAllByTestId("contact-card")
      .map((el) => el.getAttribute("data-contact-id"));
    expect(ids).toEqual(["c-new", "c-old"]);
  });
});

// The honest sync outcome (2026-08-16): a stopped sweep surfaces WHY beside
// the stamp; a clean sweep shows nothing extra.
describe("sync stopped note", () => {
  it("maps each stop reason to its copy, and clean/absent outcomes to nothing", () => {
    expect(syncStoppedKey(null)).toBeNull();
    expect(syncStoppedKey({ stopped: "" })).toBeNull();
    expect(syncStoppedKey({ stopped: "cap_or_backoff" }))
      .toBe("networking.sync.stoppedCap");
    expect(syncStoppedKey({ stopped: "rate_limited" }))
      .toBe("networking.sync.stoppedRate");
    expect(syncStoppedKey({ stopped: "auth_error" }))
      .toBe("networking.sync.stoppedAuth");
    expect(syncStoppedKey({ stopped: "batch_failed" }))
      .toBe("networking.sync.stoppedOther");
  });

  it("a refused press shows the warn note with the untouched-tail size", () => {
    h.session = {
      enabled: true,
      status: "valid",
      contact_sync_last_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
      contact_sync_last_outcome: {
        at: new Date().toISOString(),
        synced: 0, failed: 0, stopped: "cap_or_backoff", unprobed: 4,
      },
    };
    renderNetworking();
    const note = screen.getByTestId("sync-stopped-note");
    expect(note.textContent).toContain("networking.sync.stoppedCap");
    expect(note.textContent).toContain("networking.sync.notChecked");
    // ONE status at a time: the warn pill replaces the stamp, and the last
    // real sweep's time rides the pill's tooltip instead (2026-08-16).
    expect(screen.queryByTestId("last-synced-stamp")).toBeNull();
    expect(note.getAttribute("title")).toContain("networking.sync.lastSynced");
  });

  it("a clean sweep renders no note", () => {
    h.session = {
      enabled: true,
      status: "valid",
      contact_sync_last_at: new Date().toISOString(),
      contact_sync_last_outcome: {
        at: new Date().toISOString(),
        synced: 5, failed: 0, stopped: "", unprobed: 0,
      },
    };
    renderNetworking();
    expect(screen.queryByTestId("sync-stopped-note")).toBeNull();
  });
});
