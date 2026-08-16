// Queue panel for the Networking Browser tab — what the agent is doing on
// the surface, shown honestly as ONE list (maintainer, 2026-08-16): settled
// rows first (green check / warn / fail, with verbatim reasons), then the
// live op (the spinner IS the in-progress signal) carrying its declared
// step plan, then the waiting rows in grey — done, in progress, and up next
// in a single view, scrollable, with settled rows aging out after an hour.
// The step plan is the real driver sequence (see `upstream/actions.py`) and
// the steps TICK LIVE: the driver reports each completed step (`send_step`
// events, 2026-08-16 — real progress from the code driving the page), so a
// done step shows a green check, the step underway a spinner, the rest grey.
// Everything else advances only on signals we actually observe: the runner's
// `operation` state events and the networker phases. Idle is an explicit
// state — the panel never implies activity when there is none.
//
// Add-on-side by design (it names LinkedIn op kinds); the vendor-agnostic
// screencast surface it sits beside stays clean (`plugin-architecture.md`
// section 12.2: the Browser tab's queue/plan UI belongs to the add-on).

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api";
import { eventBus, type SSEEvent } from "../api/events";
import { useContacts } from "../api/queries";

/** The op kinds that drive the LinkedIn browser surface. `send`/`discover`
 *  carry a full step plan; the rest get an honest kind label + state only.
 *  Mirrors the sidecar's `BROWSER_LANE_KINDS` (runner/policy.py). */
const TRACKED_KINDS = new Set([
  "send",
  "discover",
  "contact_sync",
  "linkedin_search",
  "linkedin_login",
  "view_page",
]);

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

/** Whether `op` is still holding (or about to hold) the browser lane. The
 *  Networking surface reads this to defer the Browser tab's auto-open-home
 *  while an operation is driving the surface. */
export function opBusy(op: ActiveOp | null): boolean {
  return op != null && !TERMINAL.has(op.state);
}

export interface ActiveOp {
  id: string;
  kind: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  /** The ledger's subject label (a send → the contact's name) — the queue
   *  names WHO is being worked on from the moment the op exists, not only
   *  after its `sending` phase (maintainer, 2026-08-16). */
  label?: string;
  /** From the `sending` phase — the routed channel, decided server-side. */
  channel?: "dm" | "connection_note";
  contactId?: string;
  /** Step keys the driver has REPORTED COMPLETE (`send_step` events) — real
   *  progress from the code driving the page, in arrival order. */
  doneSteps?: string[];
  dryRun?: boolean;
  /** discover: real per-candidate signal count. */
  found?: number;
  /** discover ended asking the user to pick the company (a real pause). */
  waitingConfirm?: boolean;
  /** From the terminal phase: `sent` → true, `send_failed` → false. A send op
   *  can end `succeeded` with sent=false (a cap refusal is a domain outcome,
   *  not a crash) — the panel must not tick steps that never ran. */
  sent?: boolean;
  /** verbatim failure reason from `send_failed` (NFR-SIDE-04 wording). */
  reason?: string;
}

/** Step keys per plan (bodies live in i18n `networking.opPlan.steps`). A send
 *  shows no steps until the `sending` phase declares its routed channel —
 *  guessing DM vs invite would fabricate a plan we don't know yet. */
function stepKeys(op: ActiveOp): string[] {
  if (op.kind === "discover") return ["discover1", "discover2", "discover3"];
  if (op.kind !== "send" || !op.channel) return [];
  const prefix = op.channel === "dm" ? "dm" : "invite";
  return [1, 2, 3, 4, 5].map((n) => `${prefix}${n}`);
}

/** One op waiting behind the current one on the serialized browser lane. */
export interface QueuedOp {
  id: string;
  kind: string;
  /** The ledger's subject label (a send → the contact's name). */
  label?: string;
  /** A send's routed channel, once known — drives the row's action prefix. */
  channel?: "dm" | "connection_note";
}

/** One op that settled this session — kept on screen so a back-to-back batch
 *  stays legible after the panel moves on to the next send (maintainer,
 *  2026-08-16: three queued sends left no visible trace of who was done). */
export interface SettledOp {
  id: string;
  kind: string;
  label?: string;
  contactId?: string;
  /** A send's routed channel — drives the row's action prefix. */
  channel?: "dm" | "connection_note";
  outcome: "done" | "not_sent" | "failed" | "cancelled";
  reason?: string;
  /** When the op settled (ms epoch) — settled rows age out of the queue after
   *  `SETTLED_TTL_MS` so an old batch doesn't haunt the panel all day. */
  settledAt: number;
}

export interface BrowserOps {
  current: ActiveOp | null;
  /** Waiting ops (current excluded), lane order. */
  queued: QueuedOp[];
  /** Session-settled ops, newest first (capped + aged out after an hour). */
  settled: SettledOp[];
}

const SETTLED_CAP = 20;
/** Settled rows stay visible this long (maintainer, 2026-08-16: keep the done
 *  entries around for about an hour, then let the queue clear itself). */
const SETTLED_TTL_MS = 60 * 60_000;

/** Side-channel facts about ops the panel may need before/after they own the
 *  current slot: ledger labels, the send's contact + outcome phases. */
interface OpMeta {
  kind: string;
  label?: string;
  contactId?: string;
  sent?: boolean;
  reason?: string;
  channel?: "dm" | "connection_note";
  doneSteps?: string[];
}

/** Tracks the surface's operations off the SSE bus (with a ledger seed for ops
 *  already in flight at mount). Lifted out of the panel (2026-08-15) so the
 *  Networking surface can mount it for the WHOLE surface's lifetime — the
 *  panel used to mount only with the Browser tab, so a send started from the
 *  contact composer had already published its `sending` phase (the routed
 *  channel) before the panel existed, and the plan rendered stepless.
 *
 *  Since 2026-08-16 it tracks the whole lane, not just the head: `queued`
 *  lists the ops waiting behind the current one (a 3-person reach-out shows
 *  all 3), and `settled` keeps what finished this session on screen. The
 *  QUEUE MEMBERSHIP is driven purely by SSE `operation` events (deterministic;
 *  the ledger can lag); the ledger is fetched only to ENRICH rows with their
 *  subject labels (a queued send's contact name lives nowhere in the events
 *  until its `sending` phase). */
export function useBrowserOps(): BrowserOps {
  const [op, setOp] = useState<ActiveOp | null>(null);
  // Active tracked ops in arrival order (includes the current one; the return
  // value filters it out of `queued`).
  const [active, setActive] = useState<{ id: string; kind: string }[]>([]);
  const [settled, setSettled] = useState<SettledOp[]>([]);
  const metaRef = useRef<Map<string, OpMeta>>(new Map());
  // Bump to re-render after an async label enrich lands in metaRef.
  const [, setLabelTick] = useState(0);

  const meta = (id: string, kind?: string): OpMeta => {
    let m = metaRef.current.get(id);
    if (!m) {
      m = { kind: kind ?? "send" };
      metaRef.current.set(id, m);
    } else if (kind) {
      m.kind = kind;
    }
    return m;
  };

  // Label enrich + mount seed: the ledger is the same source the Logs surface
  // trusts. Adds any active tracked ops we never saw events for (mount during
  // a running batch) and fills subject labels for the ones we did.
  const enrich = () => {
    void api.listLedger().then((rows) => {
      let changed = false;
      const tracked = rows.filter((r) => TRACKED_KINDS.has(r.kind));
      const isLive = (r: { state: string }) => r.state === "queued" || r.state === "running";
      for (const r of tracked) {
        // Labels/channels apply to every tracked row this session knows —
        // live OR already settled. A fast-settling op (a page view lands in
        // under a second) used to miss its subject because only live rows
        // were labeled, and its settled row then read as the bare kind
        // (2026-08-16: the "Show a page" rows).
        if (!isLive(r) && !metaRef.current.has(r.id)) continue;
        const m = meta(r.id, r.kind);
        if (r.subject?.label && m.label !== r.subject.label) {
          m.label = r.subject.label;
          changed = true;
        }
        // A mid-flight send's routed channel + completed steps (the ledger's
        // progress mirror) — how a panel that mounted mid-send recovers the
        // fine steps whose SSE events fired before it existed (2026-08-16).
        const prog = r.progress;
        if (prog?.channel && m.channel !== prog.channel) {
          m.channel = prog.channel as OpMeta["channel"];
          changed = true;
        }
        if (prog?.steps && prog.steps.length > (m.doneSteps?.length ?? 0)) {
          m.doneSteps = [...prog.steps];
          changed = true;
        }
      }
      const live = tracked.filter(isLive).reverse(); // ledger is newest-first; the lane runs oldest-first
      if (live.length) {
        setActive((cur) => {
          const known = new Set(cur.map((a) => a.id));
          const missing = live.filter((r) => !known.has(r.id));
          return missing.length
            ? [...cur, ...missing.map((r) => ({ id: r.id, kind: r.kind }))]
            : cur;
        });
        setOp((cur) => {
          const head = live[0];
          return cur ?? { id: head.id, kind: head.kind, state: head.state };
        });
      }
      // Retro-fill settled rows that settled before their label arrived.
      setSettled((cur) => {
        let touched = false;
        const next = cur.map((s) => {
          const m = metaRef.current.get(s.id);
          if (m && ((!s.label && m.label) || (!s.channel && m.channel))) {
            touched = true;
            return { ...s, label: s.label ?? m.label, channel: s.channel ?? m.channel };
          }
          return s;
        });
        return touched ? next : cur;
      });
      if (changed) setLabelTick((n) => n + 1);
    }).catch(() => {
      /* the SSE stream still drives the panel; a failed enrich just means
         kind-only labels */
    });
  };
  const enrichRef = useRef(enrich);
  enrichRef.current = enrich;

  useEffect(() => {
    enrichRef.current();
  }, []);

  // Age settled rows out (the queue clears itself after roughly an hour). A
  // minute-wise sweep is plenty — the TTL is an hour, not a stopwatch.
  useEffect(() => {
    const timer = setInterval(() => {
      setSettled((cur) => {
        const fresh = cur.filter((s) => Date.now() - s.settledAt < SETTLED_TTL_MS);
        return fresh.length === cur.length ? cur : fresh;
      });
    }, 60_000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    return eventBus.subscribe((ev: SSEEvent) => {
      if (ev.type === "operation") {
        const p = ev.payload as { id: string; kind: string; state: ActiveOp["state"] };
        if (!TRACKED_KINDS.has(p.kind)) return;
        const m = meta(p.id, p.kind);
        if (TERMINAL.has(p.state)) {
          setActive((cur) => cur.filter((a) => a.id !== p.id));
          const outcome: SettledOp["outcome"] =
            p.state === "failed" ? "failed"
            : p.state === "cancelled" ? "cancelled"
            : p.kind === "send" && m.sent === false ? "not_sent"
            : "done";
          setSettled((cur) => [
            { id: p.id, kind: p.kind, label: m.label, contactId: m.contactId,
              channel: m.channel,
              outcome, reason: outcome === "done" ? undefined : m.reason,
              settledAt: Date.now() },
            ...cur.filter((s) => s.id !== p.id),
          ].slice(0, SETTLED_CAP));
          // A fast-settling op can land before its ledger subject was ever
          // fetched — go get it so the settled row is named, not bare-kind.
          if (!m.label) enrichRef.current();
        } else {
          setActive((cur) =>
            cur.some((a) => a.id === p.id) ? cur : [...cur, { id: p.id, kind: p.kind }],
          );
          // A fresh queued/running op's subject label lives only in the ledger.
          if (!m.label) enrichRef.current();
        }
        setOp((cur) => {
          if (cur && cur.id === p.id) return { ...cur, state: p.state };
          // A new op takes the panel over once the previous one has settled
          // (with one serialized lane they never truly overlap).
          if (!cur || TERMINAL.has(cur.state)) return { id: p.id, kind: p.kind, state: p.state };
          return cur;
        });
        return;
      }
      if (ev.type !== "networker") return;
      const p = ev.payload as {
        id?: string; phase?: string; contact_id?: string; channel?: "dm" | "connection_note";
        dry_run?: boolean; count?: number; reason?: string; step?: string;
      };
      const pid = p.id;
      if (!pid) return;
      // Record the send-outcome facts per op id — the settled list needs them
      // even when the op never owned the current slot.
      if (p.phase === "sending") {
        const m = meta(pid, "send");
        m.contactId = p.contact_id;
        m.channel = p.channel;
      } else if (p.phase === "send_step" && p.step) {
        const m = meta(pid, "send");
        m.doneSteps = [...(m.doneSteps ?? []), p.step];
      } else if (p.phase === "sent") {
        meta(pid).sent = true;
      } else if (p.phase === "send_failed") {
        const m = meta(pid);
        m.sent = false;
        m.reason = p.reason;
      }
      setOp((cur) => {
        if (p.phase === "sending") {
          const base: ActiveOp =
            cur && cur.id === pid ? cur : { id: pid, kind: "send", state: "running" };
          return { ...base, channel: p.channel, contactId: p.contact_id, dryRun: p.dry_run };
        }
        if (!cur || cur.id !== pid) return cur;
        if (p.phase === "send_step" && p.step) {
          return { ...cur, doneSteps: [...(cur.doneSteps ?? []), p.step] };
        }
        if (p.phase === "candidate") return { ...cur, found: (cur.found ?? 0) + 1 };
        if (p.phase === "discovered") return { ...cur, found: p.count ?? cur.found };
        if (p.phase === "needs_company_confirm") return { ...cur, waitingConfirm: true };
        if (p.phase === "sent") return { ...cur, sent: true };
        if (p.phase === "send_failed") return { ...cur, sent: false, reason: p.reason };
        return cur;
      });
    });
  }, []);

  const opMeta = op ? metaRef.current.get(op.id) : undefined;
  return {
    current: op
      ? {
          ...op,
          label: op.label ?? opMeta?.label,
          channel: op.channel ?? opMeta?.channel,
          // meta accumulates from BOTH the SSE stream and the ledger seed —
          // always the superset.
          doneSteps: (opMeta?.doneSteps?.length ?? 0) >= (op.doneSteps?.length ?? 0)
            ? opMeta?.doneSteps
            : op.doneSteps,
        }
      : null,
    queued: active
      .filter((a) => a.id !== op?.id)
      .map((a) => ({
        id: a.id,
        kind: a.kind,
        label: metaRef.current.get(a.id)?.label,
        channel: metaRef.current.get(a.id)?.channel,
      })),
    settled,
  };
}

export function BrowserOpPlan({ ops }: { ops: BrowserOps }) {
  const { t } = useTranslation();
  const contactsQ = useContacts();
  const { current, queued, settled } = ops;

  /** The row's ACTION prefix key (maintainer, 2026-08-16: a bare name says
   *  who, never what — "View:", "Connect:", "Message:", "Find employees
   *  in:"). A send names its channel only once the server routes it
   *  (`sending` phase / ledger progress); until then the honest generic
   *  "Reach out:". null = the kind label alone says it all (contact_sync). */
  const rowKeyOf = (row: {
    kind: string;
    channel?: "dm" | "connection_note";
  }): string | null => {
    switch (row.kind) {
      case "view_page":
        return "view";
      case "send":
        return row.channel === "dm"
          ? "message"
          : row.channel === "connection_note"
            ? "connect"
            : "reachOut";
      case "discover":
        return "discover";
      case "linkedin_search":
        return "search";
      case "linkedin_login":
        return "login";
      default:
        return null;
    }
  };

  /** Row label: action prefix + subject (ledger subject → contact lookup),
   *  falling back to the kind's display name when no subject is known.
   *  Nothing fabricated, best available first. */
  const labelOf = (row: {
    kind: string;
    label?: string;
    contactId?: string;
    channel?: "dm" | "connection_note";
  }): string => {
    const subject =
      row.label ??
      (row.contactId ? contactsQ.data?.find((c) => c.id === row.contactId)?.name : undefined);
    if (subject) {
      const key = rowKeyOf(row);
      if (key) return t(`networking.opPlan.row.${key}`, { name: subject });
      return subject;
    }
    return t(`networking.opPlan.kinds.${row.kind}`, { defaultValue: row.kind });
  };

  // ONE queue, read top to bottom like a checklist (maintainer, 2026-08-16:
  // done / in progress / up next in a single view, never section-switching):
  // settled rows oldest-first, then the live op with its step plan, then the
  // waiting rows. A terminal `current` is already in `settled` — rendering it
  // as live too would double-list it.
  const live = current && !TERMINAL.has(current.state) ? current : null;
  const past = [...settled].reverse();
  const idle = !live && queued.length === 0;

  // A done row's check mark says it all — no "Done" chip beside it
  // (maintainer, 2026-08-16). Only the outcomes that carry information keep
  // a label: Not sent / Failed / cancelled.
  const outcomeChip = (s: SettledOp) => {
    const cls =
      s.outcome === "done" ? "text-good"
      : s.outcome === "not_sent" ? "text-warn"
      : s.outcome === "failed" ? "text-bad" : "text-ink-4";
    const key =
      s.outcome === "not_sent" ? "notSent"
      : s.outcome === "failed" ? "failed"
      : s.outcome === "cancelled" ? "cancelled" : null;
    return { cls, label: key ? t(`networking.opPlan.${key}`) : null };
  };

  const liveSteps = live ? stepKeys(live) : [];

  return (
    <Panel>
      {idle && (
        <p className="text-[12px] font-medium text-ink-2" data-testid="browser-op-plan-idle">
          {t("networking.opPlan.idleTitle")}
        </p>
      )}
      {(past.length > 0 || !idle) && (
        <ol className="flex flex-col gap-1.5" data-testid="browser-op-queue">
          {past.map((s) => {
            const chip = outcomeChip(s);
            return (
              <li key={s.id} data-testid="queue-settled" title={s.reason || undefined}>
                <div className="flex items-center gap-2 text-[11.5px]">
                  <span
                    aria-hidden
                    className={`w-3 shrink-0 text-center text-[11px] ${chip.cls}`}
                  >
                    {s.outcome === "done" ? "✓" : s.outcome === "not_sent" ? "!" : "✗"}
                  </span>
                  <span className="truncate text-ink-2">{labelOf(s)}</span>
                  {chip.label ? (
                    <span className={`ml-auto shrink-0 text-[10px] font-medium ${chip.cls}`}>
                      {chip.label}
                    </span>
                  ) : null}
                </div>
                {/* An outcome that needs explaining keeps its verbatim reason
                    visible, not just on hover (NFR-SIDE-04 wording). */}
                {s.reason ? (
                  <p
                    className="ml-5 mt-1 rounded-md border border-bad/40 bg-bad-wash px-2 py-1 text-[11px] leading-snug text-bad"
                    data-testid="queue-settled-reason"
                  >
                    {s.reason}
                  </p>
                ) : null}
              </li>
            );
          })}

          {/* The line between done and not-done (maintainer, 2026-08-16): a
              glance says what's finished and what's next. Only when both
              sides exist — no stray rule over an all-settled or all-live
              list. */}
          {past.length > 0 && (live || queued.length > 0) ? (
            <li aria-hidden data-testid="queue-divider" className="my-0.5 border-t border-border" />
          ) : null}

          {live && (
            <li data-testid="queue-current">
              <div className="flex items-center gap-2 text-[11.5px]">
                {/* The spinner IS the in-progress signal (no placeholder text
                    row) — done rows get the check, waiting rows stay grey. */}
                <span
                  aria-hidden
                  className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border border-accent/60 border-t-transparent"
                />
                <span className="truncate font-medium text-ink">{labelOf(live)}</span>
                {live.dryRun ? (
                  <span className="ml-auto shrink-0 text-[10px] text-ink-4">
                    {t("networking.opPlan.dryRun")}
                  </span>
                ) : null}
              </div>

              {liveSteps.length > 0 && (() => {
                const done = new Set(live.doneSteps ?? []);
                // The step underway = the first one the driver hasn't
                // reported complete (only while the op is running).
                const activeKey =
                  live.state === "running"
                    ? liveSteps.find((k) => !done.has(k))
                    : undefined;
                return (
                  <ol className="ml-5 mt-1.5 flex flex-col gap-1.5" data-testid="browser-op-plan-steps">
                    {liveSteps.map((k) => (
                      <li key={k} className="flex items-start gap-2 text-[11.5px] leading-snug">
                        {done.has(k) ? (
                          <span
                            aria-hidden
                            data-testid="step-done"
                            className="mt-[1px] w-3 shrink-0 text-center text-[11px] text-good"
                          >
                            ✓
                          </span>
                        ) : k === activeKey ? (
                          <span
                            aria-hidden
                            data-testid="step-active"
                            className="mt-[3px] inline-block h-2.5 w-3 shrink-0"
                          >
                            <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border border-accent/60 border-t-transparent" />
                          </span>
                        ) : (
                          <span
                            aria-hidden
                            className="mt-[1px] w-3 shrink-0 text-center text-[11px] text-ink-4"
                          >
                            •
                          </span>
                        )}
                        <span className={done.has(k) || k === activeKey ? "text-ink-2" : "text-ink-4"}>
                          {t(`networking.opPlan.steps.${k}`)}
                        </span>
                      </li>
                    ))}
                  </ol>
                );
              })()}

              {/* Real mid-op signals, and only those. */}
              {live.kind === "discover" && (live.found ?? 0) > 0 && (
                <p className="ml-5 mt-1.5 text-[11.5px] text-ink-2" data-testid="browser-op-plan-found">
                  {t("networking.opPlan.foundSoFar", { count: live.found })}
                </p>
              )}
              {live.waitingConfirm && (
                <p className="ml-5 mt-1.5 text-[11.5px] text-warn">
                  {t("networking.opPlan.waitingCompanyConfirm")}
                </p>
              )}
              {/* The verbatim reason travels with `send_failed` even while the
                  op row is still settling (a cap refusal is an outcome, not a
                  crash). */}
              {live.reason ? (
                <p
                  className="ml-5 mt-1.5 rounded-md border border-bad/40 bg-bad-wash px-2 py-1.5 text-[11.5px] text-bad"
                  data-testid="browser-op-plan-reason"
                >
                  {live.reason}
                </p>
              ) : null}
            </li>
          )}

          {queued.map((q) => (
            <li
              key={q.id}
              data-testid="queue-waiting"
              className="flex items-center gap-2 text-[11.5px] text-ink-4"
            >
              <span aria-hidden className="w-3 shrink-0 text-center text-[11px]">
                ○
              </span>
              <span className="truncate">{labelOf(q)}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

// No panel heading (maintainer, 2026-08-16: "LinkedIn actions" said nothing) —
// the queue rows speak for themselves; idle keeps its explicit line.
function Panel({ children }: { children: React.ReactNode }) {
  return (
    <aside
      className="flex w-[280px] shrink-0 flex-col gap-2 overflow-y-auto border-l border-border bg-surface p-4"
      data-testid="browser-op-plan"
    >
      {children}
    </aside>
  );
}
