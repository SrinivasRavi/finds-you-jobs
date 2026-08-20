// Unit tests for the browser-lane queue panel's honesty rules: explicit idle,
// ONE queue view (done rows with a green check, the live op under a spinner
// with its step plan, waiting rows in grey), a send shows NO steps until the
// `sending` phase declares its routed channel, outcomes stay honest (a
// cap-refused send reads "Not sent" with the verbatim reason even though its
// op ends `succeeded`), discover's found-count follows the real per-candidate
// events, and settled rows age out after an hour. The event bus and API are
// mocked; events are driven by hand.

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  listeners: [] as ((ev: unknown) => void)[],
  ledger: [] as unknown[],
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && "name" in opts
        ? `${key}(${String(opts.name)})`
        : opts && "count" in opts
          ? `${key}(${String(opts.count)})`
          : key,
  }),
}));
vi.mock("../api/events", () => ({
  eventBus: {
    subscribe: (fn: (ev: unknown) => void) => {
      h.listeners.push(fn);
      return () => {
        h.listeners = h.listeners.filter((l) => l !== fn);
      };
    },
  },
}));
vi.mock("../api", () => ({
  api: { listLedger: () => Promise.resolve(h.ledger) },
}));
vi.mock("../api/queries", () => ({
  useContacts: () => ({
    data: [{ id: "c1", name: "Sarah Tan" }],
  }),
}));

import { BrowserOpPlan, useBrowserOps } from "./BrowserOpPlan";

// The hook + panel wired the way Networking mounts them: the hook tracks the
// lane (for the surface's whole lifetime), the panel renders what it's handed.
function Panel() {
  const ops = useBrowserOps();
  return <BrowserOpPlan ops={ops} />;
}

function emit(ev: unknown): void {
  act(() => {
    for (const l of [...h.listeners]) l(ev);
  });
}

const opEvent = (id: string, kind: string, state: string) => ({
  type: "operation",
  payload: { id, kind, state },
});
const netEvent = (payload: Record<string, unknown>) => ({
  type: "networker",
  payload,
});

afterEach(() => {
  cleanup();
  h.listeners = [];
  h.ledger = [];
  vi.useRealTimers();
});

describe("BrowserOpPlan", () => {
  it("is explicitly idle until a tracked op exists, and ignores untracked kinds", () => {
    render(<Panel />);
    expect(screen.getByTestId("browser-op-plan-idle")).toBeTruthy();
    emit(opEvent("x1", "tailor", "running")); // not a browser-surface op
    expect(screen.getByTestId("browser-op-plan-idle")).toBeTruthy();
  });

  it("a queued send reads 'Reach out:' until the server routes its channel", async () => {
    // The channel (DM vs invite) is the server's call at send time — until
    // the `sending` phase declares it, the row states the generic action,
    // never a guessed one.
    h.ledger = [
      { id: "op9", kind: "send", state: "queued", subject: { label: "Priya Patel" } },
    ];
    render(<Panel />);
    emit(opEvent("op9", "send", "queued"));
    await act(async () => {}); // the label enrich resolves
    expect(screen.getByTestId("queue-current").textContent).toContain(
      "networking.opPlan.row.reachOut(Priya Patel)",
    );
  });

  it("a fast-settling view is named retroactively from the ledger, never left as the bare kind", async () => {
    // A view lands in under a second — its subject may arrive AFTER the row
    // settled. The settle triggers a ledger fetch that back-fills the label
    // (2026-08-16: the maintainer's "Show a page" rows).
    h.ledger = [
      {
        id: "v1", kind: "view_page", state: "succeeded",
        subject: { label: "/in/sandhya-singh" },
      },
    ];
    render(<Panel />);
    emit(opEvent("v1", "view_page", "queued"));
    emit(opEvent("v1", "view_page", "succeeded"));
    await act(async () => {}); // the settle-triggered enrich resolves
    expect(screen.getByTestId("queue-settled").textContent).toContain(
      "networking.opPlan.row.view(/in/sandhya-singh)",
    );
    expect(screen.getByTestId("queue-settled").textContent).not.toContain(
      "networking.opPlan.kinds.view_page",
    );
  });

  it("a queued page-view is a tracked row: kind label, no fabricated steps", () => {
    // view_page (2026-08-16): the user's own "open in LinkedIn" click rides
    // the same lane — the panel names it honestly and invents no plan.
    render(<Panel />);
    emit(opEvent("v1", "view_page", "queued"));
    const current = screen.getByTestId("queue-current");
    expect(current.textContent).toContain("networking.opPlan.kinds.view_page");
    expect(screen.queryByTestId("browser-op-plan-steps")).toBeNull();

    emit(opEvent("v1", "view_page", "running"));
    expect(screen.queryByTestId("browser-op-plan-steps")).toBeNull();
    emit(opEvent("v1", "view_page", "succeeded"));
    expect(screen.queryByTestId("queue-current")).toBeNull();
    expect(screen.getByTestId("queue-settled").textContent).toContain(
      "networking.opPlan.kinds.view_page",
    );
  });

  it("shows the live send WITHOUT steps until the routed channel is declared, then the right plan", () => {
    render(<Panel />);
    emit(opEvent("op1", "send", "queued"));
    expect(screen.getByTestId("queue-current")).toBeTruthy();
    expect(screen.queryByTestId("browser-op-plan-steps")).toBeNull();

    emit(opEvent("op1", "send", "running"));
    emit(netEvent({ id: "op1", phase: "sending", contact_id: "c1", channel: "dm" }));
    const steps = screen.getByTestId("browser-op-plan-steps");
    expect(steps.textContent).toContain("networking.opPlan.steps.dm3");
    expect(steps.textContent).not.toContain("invite");
    // The contact's name rides the real `sending` signal, prefixed with the
    // routed ACTION (maintainer, 2026-08-16: a bare name says who, not what).
    expect(screen.getByTestId("queue-current").textContent).toContain(
      "networking.opPlan.row.message(Sarah Tan)",
    );
    // No step reported yet → nothing ticked; the first step carries the
    // spinner (the driver is on it).
    expect(steps.textContent).not.toContain("✓");
    expect(steps.querySelectorAll('[data-testid="step-done"]')).toHaveLength(0);
    expect(steps.querySelectorAll('[data-testid="step-active"]')).toHaveLength(1);

    // REAL progress (2026-08-16): the driver reports steps as it finishes
    // them — done steps tick green, the spinner moves to the next one.
    emit(netEvent({ id: "op1", phase: "send_step", step: "dm1" }));
    emit(netEvent({ id: "op1", phase: "send_step", step: "dm2" }));
    const ticked = screen.getByTestId("browser-op-plan-steps");
    expect(ticked.querySelectorAll('[data-testid="step-done"]')).toHaveLength(2);
    expect(ticked.querySelectorAll('[data-testid="step-active"]')).toHaveLength(1);
  });

  it("a delivered send settles into a check-only row (no Done chip)", () => {
    render(<Panel />);
    emit(opEvent("op1", "send", "running"));
    emit(netEvent({ id: "op1", phase: "sending", contact_id: "c1", channel: "connection_note" }));
    emit(netEvent({ id: "op1", phase: "sent" }));
    emit(opEvent("op1", "send", "succeeded"));
    // The live row is gone (nothing is running); the settled row carries the
    // check + the contact's name — the check mark IS the done signal
    // (maintainer, 2026-08-16: no redundant "Done" text beside it).
    expect(screen.queryByTestId("queue-current")).toBeNull();
    const row = screen.getByTestId("queue-settled");
    expect(row.textContent).toContain("✓");
    // Prefixed with its routed action — an invite row reads "Connect: name".
    expect(row.textContent).toContain("networking.opPlan.row.connect(Sarah Tan)");
    expect(row.textContent).not.toContain("networking.opPlan.done");
  });

  it("a rule separates settled rows from the live/waiting block, and only then", () => {
    render(<Panel />);
    emit(opEvent("op1", "send", "running"));
    emit(netEvent({ id: "op1", phase: "sending", contact_id: "c1", channel: "dm" }));
    // Live only, nothing settled: no divider.
    expect(screen.queryByTestId("queue-divider")).toBeNull();
    emit(netEvent({ id: "op1", phase: "sent" }));
    emit(opEvent("op1", "send", "succeeded"));
    // Settled only, nothing live or waiting: still no divider.
    expect(screen.queryByTestId("queue-divider")).toBeNull();
    emit(opEvent("op2", "send", "queued"));
    // Done above, up-next below: the line between them appears.
    expect(screen.getByTestId("queue-divider")).toBeTruthy();
  });

  it("a refused send settles as 'Not sent' with the verbatim reason visible", () => {
    render(<Panel />);
    emit(opEvent("op1", "send", "running"));
    emit(netEvent({ id: "op1", phase: "sending", channel: "connection_note" }));
    emit(
      netEvent({ id: "op1", phase: "send_failed", reason: "weekly invitation limit reached" }),
    );
    emit(opEvent("op1", "send", "succeeded")); // a cap refusal is an outcome, not a crash
    const row = screen.getByTestId("queue-settled");
    expect(row.textContent).toContain("networking.opPlan.notSent");
    expect(row.textContent).not.toContain("✓");
    expect(screen.getByTestId("queue-settled-reason").textContent).toBe(
      "weekly invitation limit reached",
    );
  });

  it("a dry run is labeled and never reads as a delivered send", () => {
    render(<Panel />);
    emit(opEvent("op1", "send", "running"));
    emit(netEvent({ id: "op1", phase: "sending", channel: "dm", dry_run: true }));
    expect(screen.getByTestId("queue-current").textContent).toContain(
      "networking.opPlan.dryRun",
    );
    emit(netEvent({ id: "op1", phase: "send_failed", reason: "" }));
    emit(opEvent("op1", "send", "succeeded"));
    expect(screen.getByTestId("queue-settled").textContent).toContain(
      "networking.opPlan.notSent",
    );
  });

  it("discover advances on its real signals: found-count and the company-confirm pause", () => {
    render(<Panel />);
    emit(opEvent("op2", "discover", "running"));
    expect(screen.getByTestId("browser-op-plan-steps").textContent).toContain(
      "networking.opPlan.steps.discover1",
    );
    emit(netEvent({ id: "op2", phase: "candidate", contact_id: "a" }));
    emit(netEvent({ id: "op2", phase: "candidate", contact_id: "b" }));
    expect(screen.getByTestId("browser-op-plan-found").textContent).toContain("(2)");

    emit(netEvent({ id: "op2", phase: "needs_company_confirm" }));
    expect(screen.getByTestId("queue-current").textContent).toContain(
      "networking.opPlan.waitingCompanyConfirm",
    );
  });

  it("shows the WHOLE batch in one view: done, in progress, and waiting rows together", () => {
    render(<Panel />);
    // op1 delivered; op2 running; op3 waiting — the maintainer's 3-person batch.
    emit(opEvent("op1", "send", "running"));
    emit(netEvent({ id: "op1", phase: "sending", contact_id: "c1", channel: "dm" }));
    emit(netEvent({ id: "op1", phase: "sent" }));
    emit(opEvent("op1", "send", "succeeded"));
    emit(opEvent("op2", "send", "running"));
    emit(netEvent({ id: "op2", phase: "sending", channel: "connection_note" }));
    emit(opEvent("op3", "send", "queued"));

    const queue = screen.getByTestId("browser-op-queue");
    const rows = [
      ...queue.querySelectorAll(
        '[data-testid="queue-settled"],[data-testid="queue-current"],[data-testid="queue-waiting"]',
      ),
    ].map((el) => el.getAttribute("data-testid"));
    // Checklist order: past → present → future, one list, no section switching.
    expect(rows).toEqual(["queue-settled", "queue-current", "queue-waiting"]);
    expect(screen.getByTestId("queue-settled").textContent).toContain("Sarah Tan");
    // The live row shows the invite plan; the waiting row is label-only grey.
    expect(screen.getByTestId("browser-op-plan-steps").textContent).toContain("invite1");
    expect(screen.getByTestId("queue-waiting").textContent).toContain(
      "networking.opPlan.kinds.send",
    );
  });

  it("the lane advancing moves rows through the one queue (no double-listing)", () => {
    render(<Panel />);
    emit(opEvent("op1", "send", "running"));
    emit(netEvent({ id: "op1", phase: "sending", contact_id: "c1", channel: "dm" }));
    emit(opEvent("op2", "send", "queued"));
    expect(screen.getAllByTestId("queue-waiting")).toHaveLength(1);

    emit(netEvent({ id: "op1", phase: "sent" }));
    emit(opEvent("op1", "send", "succeeded"));
    emit(opEvent("op2", "send", "running"));
    // op2 is live now (no steps yet — channel unknown); op1 settled; none waiting.
    expect(screen.getAllByTestId("queue-settled")).toHaveLength(1);
    expect(screen.getByTestId("queue-current")).toBeTruthy();
    expect(screen.queryByTestId("queue-waiting")).toBeNull();
    expect(screen.queryByTestId("browser-op-plan-steps")).toBeNull();
  });

  it("settled rows age out after an hour (the queue clears itself)", () => {
    vi.useFakeTimers();
    render(<Panel />);
    emit(opEvent("op1", "send", "running"));
    emit(netEvent({ id: "op1", phase: "sending", contact_id: "c1", channel: "dm" }));
    emit(netEvent({ id: "op1", phase: "sent" }));
    emit(opEvent("op1", "send", "succeeded"));
    expect(screen.getByTestId("queue-settled")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(59 * 60_000);
    });
    expect(screen.getByTestId("queue-settled")).toBeTruthy(); // still fresh

    act(() => {
      vi.advanceTimersByTime(2 * 60_000);
    });
    expect(screen.queryByTestId("queue-settled")).toBeNull();
    expect(screen.getByTestId("browser-op-plan-idle")).toBeTruthy();
  });
});
