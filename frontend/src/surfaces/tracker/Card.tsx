// Tracker kanban card + its slot/priority tags (extracted from Tracker.tsx
// 2026-07-25, F-M6 monolith split — pure move, zero behavior change). Memoized:
// `app` is query-structural-sharing stable, `menuOpen` is a primitive, and the
// callbacks are id/app-passing useCallbacks at the root — so a search keystroke
// or an open menu no longer re-renders every card on the board.

import { memo } from "react";
import { useTranslation } from "react-i18next";

import type { Application, Priority, Stage } from "../../api/types";
import type { ResumeModalKind } from "../../popups/ResumeModal";
import { applyRunDisplay } from "../../shell/applyRunDisplay";
import { Avatar } from "../../shell/Avatar";
import { Icon } from "../../shell/icons";
import { daysBetween, scoreTier } from "../jobFormat";

const PRIORITY_CLS: Record<Priority, string> = {
  P0: "bg-bad-wash text-bad",
  P1: "bg-warn-wash text-warn",
  P2: "bg-accent-wash text-accent",
  P3: "bg-surface-3 text-ink-3",
};

function PriorityChip({ p }: { p: Priority }) {
  const { t } = useTranslation();
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${PRIORITY_CLS[p] ?? PRIORITY_CLS.P3}`}>
      {t("tracker.priorityChip", { p })}
    </span>
  );
}

// Referrals slot renders the canonical FR-NW-01 pill: grey=notStarted(none),
// grey+spinner=finding, yellow=pending, yellow+spinner=sending, green=reachedOut,
// red=failed. Maps the backend enum onto the shared PacketSlotTag state keys.
// Restored 2026-07-16 (the referral-outreach backend now exists).
const REFERRALS_SLOT_STATE: Record<Application["referrals_state"], string> = {
  none: "none",
  finding: "finding",
  pending: "pending",
  sending: "sending",
  reachedOut: "approved",
  failed: "failed",
};

// The Apply slot maps the latest Apply Run's status (applier-as-built.md section 8.2) onto the
// shared PacketSlotTag state keys: none→"Apply" (grey), waiting_for_packet/
// running→"Applying…" (grey+spinner), ready_for_human→"Review & submit"
// (yellow), submitted→"Submitted" (green check), and the honest non-success
// terminals→"Retry" (red). The table itself lives in shell/applyRunDisplay,
// shared with the Applier panel's phase pill (duplication audit D-F14).

// Stages where the job has already been applied to — the Apply slot must not
// start a fresh run there (2026-07-17 dogfood).
const POST_APPLICATION: Stage[] = ["Applied", "Interviewing", "Offer", "Rejected"];

const SLOT_SPINNER = new Set(["generating", "finding", "sending"]);
const SLOT_CHECK = new Set(["ready", "approved"]);

function PacketSlotTag({ label, state }: { label: string; state: string }) {
  const map: Record<string, string> = {
    ready: "border-good bg-good-wash text-good",
    approved: "border-good bg-good-wash text-good",
    generating: "border-border-2 bg-surface-2 text-ink-3",
    finding: "border-border-2 bg-surface-2 text-ink-3",
    pending: "border-warn bg-warn-wash text-warn",
    sending: "border-warn bg-warn-wash text-warn",
    none: "border-border-2 bg-surface text-ink-3",
    failed: "border-bad bg-bad-wash text-bad",
  };
  const spinnerTint = state === "sending" ? "border-warn border-t-transparent" : "border-border-2 border-t-accent";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${map[state] ?? map.none}`}
    >
      {SLOT_SPINNER.has(state) ? (
        <span className={`inline-block h-2 w-2 animate-spin rounded-full border ${spinnerTint}`} />
      ) : SLOT_CHECK.has(state) ? (
        <Icon name="check" size={10} strokeWidth={3} />
      ) : null}
      {label}
    </span>
  );
}

/** Days since ISO timestamp, as "3d" / "<1d" (US-TR-01 days-in-column). */
function daysIn(iso: string): string {
  const d = daysBetween(iso, "floor");
  return d <= 0 ? "<1d" : `${d}d`;
}

export const Card = memo(function Card({
  app,
  onOpen,
  onDragStart,
  onDragEnd,
  onSlot,
  onMenu,
  menuOpen,
}: {
  app: Application;
  onOpen: (id: string) => void;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  onSlot: (app: Application, kind: ResumeModalKind | "refs" | "apply") => void;
  onMenu: (id: string, anchor: DOMRect) => void;
  menuOpen: boolean;
}) {
  const { t } = useTranslation();
  const tier = app.job.score ? scoreTier(app.job.score.score_0_100) : null;
  // Defensive: an apply-run status this build doesn't know yet must degrade to
  // the grey "Apply" slot, never crash the whole board render (2026-07-24
  // unsafe-lookup audit).
  const applySlot = applyRunDisplay(app.apply_run_status);
  return (
    <div
      draggable
      onDragStart={() => onDragStart(app.id)}
      onDragEnd={onDragEnd}
      onClick={() => onOpen(app.id)}
      data-testid="tracker-card"
      className={
        "group cursor-pointer rounded-lg border bg-surface p-3 shadow-sm transition " +
        // While its menu is open, the card lifts above the dim layer so only
        // it and the menu read highlighted (maintainer 2026-07-22 #5).
        (menuOpen
          ? "relative z-50 border-accent ring-1 ring-accent"
          : "border-border hover:border-border-2")
      }
    >
      <div className="flex items-start gap-2">
        <Avatar name={app.job.company} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12.5px] font-semibold text-ink" data-testid="card-title">
            {app.job.title}
          </div>
          <div className="truncate text-[11px] text-ink-3">{app.job.company}</div>
        </div>
        {/* Persistent vertical ⋮ (maintainer 2026-07-22 #5) — always visible,
            never hover-revealed; long titles wrap around it fine. */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            onMenu(app.id, e.currentTarget.getBoundingClientRect());
          }}
          data-testid="card-menu-btn"
          className="text-ink-4 hover:text-ink"
          aria-label={t("tracker.card.menu")}
        >
          <Icon name="moreV" size={16} strokeWidth={2} />
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {app.job.score ? (
          <span className={`text-[11px] font-semibold ${tier?.text}`}>
            {app.job.score.score_0_100}
          </span>
        ) : app.origin === "manual" ? (
          // A manually-logged card is never scored (they already applied) — mark
          // its provenance instead of showing a "Pending" score that never lands.
          <span
            data-testid="card-manual-badge"
            title={t("tracker.card.manualTitle")}
            className="rounded-full border border-border-2 bg-surface-2 px-1.5 py-0.5 text-[9.5px] font-medium text-ink-3"
          >
            {t("tracker.card.manual")}
          </span>
        ) : (
          <span className="rounded-full border border-border-2 bg-surface-2 px-1.5 py-0.5 text-[9.5px] text-ink-3">
            {t("tracker.card.pending")}
          </span>
        )}
        <PriorityChip p={app.priority} />
      </div>
      {/* Three action slots — Resume · Cover letter · Referrals (US-TR-05).
          Referrals restored 2026-07-16 — wired to real referrals_state +
          opens the find-referrals popup. */}
      <div className="mt-2 flex flex-wrap items-center gap-1" onClick={(e) => e.stopPropagation()}>
        {/* A manual card with an uploaded resume/cover shows a green "present"
            slot; clicking opens the read-only submitted-doc viewer instead of
            the generate flow (FR-TR manual-add). */}
        <button onClick={() => onSlot(app, "tailored")} data-testid="card-resume-slot">
          <PacketSlotTag
            label={t("tracker.card.resume")}
            state={
              app.documents.some((d) => d.kind === "tailored_resume")
                ? "approved"
                : app.packet_resume_state
            }
          />
        </button>
        <button onClick={() => onSlot(app, "cover")} data-testid="card-cover-slot">
          <PacketSlotTag
            label={t("tracker.card.coverLetter")}
            state={
              app.documents.some((d) => d.kind === "cover_letter")
                ? "approved"
                : app.packet_cover_state
            }
          />
        </button>
        <button onClick={() => onSlot(app, "refs")} data-testid="card-referrals-slot">
          <PacketSlotTag label={t("tracker.card.referrals")} state={REFERRALS_SLOT_STATE[app.referrals_state] ?? "none"} />
        </button>
        {/* Apply slot (applier-as-built.md section 8.1/section 8.2) — starts a run (or reopens the
            bound one) and opens the companion panel. A card already past
            application (Applied/Interviewing/Offer/Rejected) with no run can't
            start one — you don't apply to a job you've already applied to
            (2026-07-17 dogfood); it shows a static "Applied" and is inert. An
            existing run stays reviewable in any stage. */}
        {POST_APPLICATION.includes(app.stage) && app.apply_run_status === "none" ? (
          <span data-testid="card-apply-slot">
            <PacketSlotTag label={t("tracker.applySlot.applied")} state="approved" />
          </span>
        ) : (
          <button onClick={() => onSlot(app, "apply")} data-testid="card-apply-slot">
            <PacketSlotTag label={t(applySlot.slotKey)} state={applySlot.slotState} />
          </button>
        )}
      </div>
      {/* days-in-column + last-touched (US-TR-01) */}
      <div className="mt-2 text-[10px] text-ink-4" data-testid="card-timestamps">
        {t("tracker.card.timestamps", {
          days: daysIn(app.created_at),
          touched: app.updated_at.slice(5, 10).replace("-", "/"),
        })}
      </div>
    </div>
  );
});
