// Application Tracker (US-TR-01..10) — 6-column kanban, card moves w/ Applied
// freeze guardrail, detail modal (Overview/Notes/Scoring/Activity/Networking),
// 3 per-card action slots (incl. the find-referrals popup off the Referrals
// slot), 3-dot menu, archive modal, search/priority/hide-rejected filters,
// priority chips. Ports jobs-tracker.html.
//
// Trimmed from the prior repo (no Applier/save-time-prep surface on this
// sidecar yet): no Apply button, no Applier preview screenshot / run-summary
// block. See inline comments at each cut. The Referrals slot + Networking tab
// were restored 2026-07-16 (the referral-outreach backend now exists).

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useAddManualApplication,
  useApplicationActivity,
  useApplicationNetworking,
  useApplications,
  useArchived,
  useArchiveApplication,
  useGeneratePacket,
  useJobPreview,
  useLinkedInSession,
  useMoveApplication,
  usePatchArtifact,
  useProfile,
  useReturnToBoard,
  useSetPriority,
  useStartApply,
  useUnarchiveApplication,
  useUpdateApplication,
} from "../api/queries";
import { api } from "../api/index";
import i18n from "../i18n";
import { HeaderAddButton, HeaderDeletedButton } from "../shell/HeaderAddButton";
import type {
  Application,
  ApplicationDocument,
  Job,
  JobDraft,
  ManualApplicationInput,
  Priority,
  Stage,
} from "../api/types";
import { JobTombstonedError, STAGES } from "../api/types";

// Stages a manually-logged application can land in — it's already been applied
// to, so it starts at Applied (or later); the pre-submission columns don't apply.
const MANUAL_STAGES: ManualApplicationInput["stage"][] = [
  "Applied",
  "Interviewing",
  "Offer",
  "Rejected",
];

// Translation keys for the attached-document slots' human labels (the artifact
// kind vocabulary).
const DOC_KIND_KEY: Record<ApplicationDocument["kind"], string> = {
  tailored_resume: "tracker.docKind.tailored_resume",
  cover_letter: "tracker.docKind.cover_letter",
};

/** Download an attached document (authed fetch → object URL → save). The bearer
 *  token can't ride on a plain href, so we fetch the blob and click a temp link. */
async function downloadDocument(doc: ApplicationDocument): Promise<void> {
  const blob = await api.fetchDocument(doc.document_id);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = doc.filename || i18n.t(DOC_KIND_KEY[doc.kind]);
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
import { ApplierPanel } from "../popups/ApplierPanel";
import { GuidanceDialog } from "../popups/GuidanceDialog";
import { ReferralsModal } from "../popups/ReferralsModal";
import { ResumeModal, type ResumeModalKind } from "../popups/ResumeModal";
import { Icon } from "../shell/icons";
import { Chip, FilterBar, FilterGroup, FilterSep, SearchBox } from "../shell/FilterRow";
import { Markdown } from "../shell/Markdown";
import { Modal } from "../shell/Modal";
import { initials, scoreTier, workLabel } from "./jobFormat";

const PRIORITY_CLS: Record<Priority, string> = {
  P0: "bg-bad-wash text-bad",
  P1: "bg-warn-wash text-warn",
  P2: "bg-accent-wash text-accent",
  P3: "bg-surface-3 text-ink-3",
};

function PriorityChip({ p }: { p: Priority }) {
  const { t } = useTranslation();
  return (
    <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-medium ${PRIORITY_CLS[p]}`}>
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

// Apply slot maps the latest Apply Run's status (applier.md §8.2) onto the
// shared PacketSlotTag state keys: none→"Apply" (grey), waiting_for_packet/
// running→"Applying…" (grey+spinner), ready_for_human→"Review & submit"
// (yellow), submitted→"Submitted" (green check), and the honest non-success
// terminals→"Retry" (red).
const APPLY_SLOT: Record<Application["apply_run_status"], { labelKey: string; state: string }> = {
  queued: { labelKey: "tracker.applySlot.applying", state: "generating" },
  none: { labelKey: "tracker.applySlot.apply", state: "none" },
  waiting_for_packet: { labelKey: "tracker.applySlot.applying", state: "generating" },
  running: { labelKey: "tracker.applySlot.applying", state: "generating" },
  ready_for_human: { labelKey: "tracker.applySlot.review", state: "pending" },
  submitted: { labelKey: "tracker.applySlot.submitted", state: "approved" },
  blocked: { labelKey: "tracker.applySlot.retry", state: "failed" },
  timed_out: { labelKey: "tracker.applySlot.retry", state: "failed" },
  interrupted: { labelKey: "tracker.applySlot.retry", state: "failed" },
  failed: { labelKey: "tracker.applySlot.retry", state: "failed" },
};

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

function Card({
  app,
  onOpen,
  onDragStart,
  onDragEnd,
  onSlot,
  onMenu,
  menuOpen,
}: {
  app: Application;
  onOpen: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  onSlot: (kind: ResumeModalKind | "refs" | "apply") => void;
  onMenu: (anchor: DOMRect) => void;
  menuOpen: boolean;
}) {
  const { t } = useTranslation();
  const tier = app.job.score ? scoreTier(app.job.score.score_0_100) : null;
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onOpen}
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
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded bg-surface-2 text-[11px] font-semibold text-ink-2">
          {initials(app.job.company)}
        </div>
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
            onMenu(e.currentTarget.getBoundingClientRect());
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
          <span className={`font-mono text-[11px] font-semibold ${tier?.text}`}>
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
          <span className="rounded-full border border-border-2 bg-surface-2 px-1.5 py-0.5 font-mono text-[9.5px] text-ink-3">
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
        <button onClick={() => onSlot("tailored")}>
          <PacketSlotTag
            label={t("tracker.card.resume")}
            state={
              app.documents.some((d) => d.kind === "tailored_resume")
                ? "approved"
                : app.packet_resume_state
            }
          />
        </button>
        <button onClick={() => onSlot("cover")}>
          <PacketSlotTag
            label={t("tracker.card.coverLetter")}
            state={
              app.documents.some((d) => d.kind === "cover_letter")
                ? "approved"
                : app.packet_cover_state
            }
          />
        </button>
        <button onClick={() => onSlot("refs")} data-testid="card-referrals-slot">
          <PacketSlotTag label={t("tracker.card.referrals")} state={REFERRALS_SLOT_STATE[app.referrals_state]} />
        </button>
        {/* Apply slot (applier.md §8.1/§8.2) — starts a run (or reopens the
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
          <button onClick={() => onSlot("apply")} data-testid="card-apply-slot">
            <PacketSlotTag
              label={t(APPLY_SLOT[app.apply_run_status].labelKey)}
              state={APPLY_SLOT[app.apply_run_status].state}
            />
          </button>
        )}
      </div>
      {/* days-in-column + last-touched (US-TR-01) */}
      <div className="mt-2 font-mono text-[10px] text-ink-4" data-testid="card-timestamps">
        {t("tracker.card.timestamps", {
          days: daysIn(app.created_at),
          touched: app.updated_at.slice(5, 10).replace("-", "/"),
        })}
      </div>
    </div>
  );
}

/** Days since ISO timestamp, as "3d" / "<1d" (US-TR-01 days-in-column). */
function daysIn(iso: string): string {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return d <= 0 ? "<1d" : `${d}d`;
}

/** Activity timestamps read naturally — "7 July 2026, 00:20" (local time). */
function formatActivityAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  const date = d.toLocaleDateString("en-GB", {
    day: "numeric", month: "long", year: "numeric",
  });
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${date}, ${hh}:${mm}`;
}

export function Tracker() {
  const { t } = useTranslation();
  const { data: apps = [] } = useApplications();
  const { data: archived = [] } = useArchived();
  const { data: profile } = useProfile();
  const move = useMoveApplication();
  const setPriority = useSetPriority();
  const updateApp = useUpdateApplication();
  const archive = useArchiveApplication();
  const returnToBoard = useReturnToBoard();
  const genPacket = useGeneratePacket();
  const patchArtifact = usePatchArtifact();
  const startApply = useStartApply();

  const [search, setSearch] = useState("");
  const [priorityFilter, setPriorityFilter] = useState<Priority | "ALL">("ALL");
  const [sourceFilter, setSourceFilter] = useState<"ALL" | "discovered" | "manual">("ALL");
  const [hideRejected, setHideRejected] = useState(false);
  const [showAddApp, setShowAddApp] = useState(false);
  const [dragId, setDragId] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [showArchive, setShowArchive] = useState(false);
  const [popup, setPopup] = useState<{ kind: ResumeModalKind; appId: string } | null>(null);
  const [guidance, setGuidance] = useState<{ appId: string; label: string } | null>(null);
  // Card ⋮ menu: id + the button's viewport rect, so the menu opens anchored
  // beside the button (popover, not a modal — maintainer 2026-07-22 #5).
  const [menu, setMenu] = useState<{ id: string; anchor: DOMRect } | null>(null);
  // REMOVED: applyId (ApplyModal — no Applier surface on this sidecar yet).
  // referralsAppId restored 2026-07-16 (the find-referrals popup).
  const [referralsAppId, setReferralsAppId] = useState<string | null>(null);
  // The Applier companion panel, bound to one Apply Run (applier.md §8.2).
  const [applierPanel, setApplierPanel] = useState<{ appId: string; runId: string } | null>(null);
  const [alert, setAlert] = useState<string | null>(null);
  // Pending drag INTO a frozen column (Applied+), held for the confirm dialog
  // below — that move can't be dragged back (2026-07-15 maintainer request;
  // replaces the earlier Saved → Seeking Referral dialog, which guarded a
  // freely reversible move).
  const [pendingFrozenMove, setPendingFrozenMove] =
    useState<{ id: string; stage: Stage } | null>(null);

  const filtered = useMemo(() => {
    return apps.filter((a) => {
      const q = search.toLowerCase();
      const hit =
        !q ||
        a.job.title.toLowerCase().includes(q) ||
        a.job.company.toLowerCase().includes(q) ||
        a.job.location.toLowerCase().includes(q);
      const pri = priorityFilter === "ALL" || a.priority === priorityFilter;
      const src = sourceFilter === "ALL" || a.origin === sourceFilter;
      return hit && pri && src;
    });
  }, [apps, search, priorityFilter, sourceFilter]);

  const columns = hideRejected ? STAGES.filter((s) => s !== "Rejected") : STAGES;
  const byStage = (s: Stage) => filtered.filter((a) => a.stage === s);

  function onDrop(stage: Stage) {
    if (!dragId) return;
    const app = apps.find((a) => a.id === dragId);
    if (!app) return;
    const frozen: Stage[] = ["Applied", "Interviewing", "Offer", "Rejected"];
    const backward: Stage[] = ["Saved", "Seeking Referral"];
    if (frozen.includes(app.stage) && backward.includes(stage)) {
      setAlert(t("tracker.backwardAlert"));
      setDragId(null);
      return;
    }
    // Dragging INTO Applied+ crosses a confirm dialog (2026-07-15 maintainer
    // request): once a card is in a frozen column it can't be dragged back to
    // Saved or Seeking Referral, so a user just playing with cards must be
    // warned before the one-way door — moves between pre-submission columns
    // stay friction-free.
    if (backward.includes(app.stage) && frozen.includes(stage)) {
      setPendingFrozenMove({ id: dragId, stage });
      setDragId(null);
      return;
    }
    move.mutate({ id: dragId, stage });
    setDragId(null);
  }

  // Apply slot (applier.md §8.1): a card with no run starts one (the click IS
  // the action — no pre-Apply confirm) and binds the companion to the returned
  // run; a card that already has a run just reopens the companion to it (its
  // snapshot drives the panel, incl. the Retry / Review & submit states).
  async function onApplyClick(app: Application) {
    if (app.apply_run_id) {
      setApplierPanel({ appId: app.id, runId: app.apply_run_id });
      return;
    }
    const run = await Promise.resolve(startApply.mutateAsync({ applicationId: app.id }));
    if (run) setApplierPanel({ appId: app.id, runId: run.id });
  }

  const detail = apps.find((a) => a.id === detailId) ?? null;
  const popupApp = popup ? apps.find((a) => a.id === popup.appId) : undefined;

  return (
    <>
      {/* Row 1 — actions that change what LEAVES this board (mirrors the Job
          Board's top row). Applications only removes via the archive. */}
      <header className="flex min-h-[48px] items-center border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">{t("tracker.title")}</h1>
        <div className="ml-auto flex items-center gap-3 py-1.5">
          <HeaderDeletedButton
            label={t("tracker.deletedApplications")}
            count={archived.length}
            onClick={() => setShowArchive(true)}
            testid="archive-btn"
          />
          <HeaderAddButton
            label={t("tracker.addApplication")}
            onClick={() => setShowAddApp(true)}
            testid="add-application-btn"
          />
        </div>
      </header>

      {/* Row 2 — view modifiers (mirrors the Job Board filter row): labeled
          chip groups + "|" separators + trailing Search, all right-aligned. */}
      <FilterBar>
        <FilterGroup label={t("tracker.filters.priorities")} id="filter-priorities">
          {(["ALL", "P0", "P1", "P2", "P3"] as const).map((p) => (
            <Chip
              key={p}
              active={priorityFilter === p}
              onClick={() => setPriorityFilter(p)}
            >
              {p === "ALL" ? t("tracker.filters.all") : p}
            </Chip>
          ))}
        </FilterGroup>
        <FilterSep />
        <FilterGroup label={t("tracker.filters.source")} id="filter-source">
          {(
            [
              ["ALL", "tracker.filters.all"],
              ["discovered", "tracker.filters.foundByFyj"],
              ["manual", "tracker.filters.addedManually"],
            ] as const
          ).map(([value, labelKey]) => (
            <Chip
              key={value}
              active={sourceFilter === value}
              onClick={() => setSourceFilter(value)}
              testid={`source-${value}`}
            >
              {t(labelKey)}
            </Chip>
          ))}
        </FilterGroup>
        <FilterSep />
        <Chip
          active={hideRejected}
          onClick={() => setHideRejected((v) => !v)}
          testid="hide-rejected"
        >
          {t("tracker.filters.hideRejected")}
        </Chip>
        <FilterSep />
        <SearchBox
          value={search}
          onChange={setSearch}
          placeholder={t("tracker.filters.search")}
          testid="tracker-search"
        />
      </FilterBar>

      {/* Kanban */}
      <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto bg-canvas p-4 no-scrollbar">
        {columns.map((stage) => {
          const cards = byStage(stage);
          return (
            <div
              key={stage}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => onDrop(stage)}
              data-testid={`col-${stage.replace(/\s+/g, "-")}`}
              className="flex w-[280px] shrink-0 flex-col rounded-xl bg-surface-2/60"
            >
              <div className="flex items-center justify-between px-3 py-2">
                <span className="text-[12px] font-semibold text-ink-2">{t(`tracker.stage.${stage}`)}</span>
                <span className="rounded bg-surface-3 px-1.5 font-mono text-[11px] text-ink-3">
                  {cards.length}
                </span>
              </div>
              <div className="flex flex-1 flex-col gap-2 overflow-y-auto px-2 pb-3">
                {cards.length === 0 ? (
                  <p className="px-1 py-2 text-[11px] text-ink-4">
                    {stage === "Saved" ? t("tracker.emptySaved") : "—"}
                  </p>
                ) : (
                  cards.map((app) => (
                    <Card
                      key={app.id}
                      app={app}
                      onOpen={() => setDetailId(app.id)}
                      onDragStart={() => setDragId(app.id)}
                      onDragEnd={() => setDragId(null)}
                      onSlot={(kind) => {
                        if (kind === "refs") {
                          // Open the find-referrals popup (US-NW-09). It handles
                          // connected / drafts-only / no-session states internally.
                          setReferralsAppId(app.id);
                          return;
                        }
                        if (kind === "apply") {
                          void onApplyClick(app);
                          return;
                        }
                        setPopup({ kind, appId: app.id });
                      }}
                      onMenu={(anchor) => setMenu({ id: app.id, anchor })}
                      menuOpen={menu?.id === app.id}
                    />
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Backward-move alert */}
      {alert ? (
        <div
          role="alert"
          className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-bad/40 bg-bad-wash px-4 py-2 text-[12.5px] text-bad shadow-lg"
          onAnimationEnd={() => setAlert(null)}
        >
          {alert}
          <button onClick={() => setAlert(null)} className="ml-3 underline">
            {t("tracker.dismiss")}
          </button>
        </div>
      ) : null}

      {/* One-way-door confirm dialog: dragging into Applied+ (2026-07-15) */}
      {pendingFrozenMove ? (
        <Modal
          title={t("tracker.frozenMove.title", { stage: t(`tracker.stage.${pendingFrozenMove.stage}`) })}
          onClose={() => setPendingFrozenMove(null)}
          width={440}
        >
          <div className="space-y-4 px-5 py-4" data-testid="frozen-move-confirm">
            <p className="text-[13px] leading-relaxed text-ink-2">
              {t("tracker.frozenMove.body", {
                stage: t(`tracker.stage.${pendingFrozenMove.stage}`),
                status:
                  pendingFrozenMove.stage === "Applied"
                    ? t("tracker.frozenMove.statusSubmitted")
                    : t("tracker.frozenMove.statusAt", {
                        stage: t(`tracker.stage.${pendingFrozenMove.stage}`),
                      }),
              })}
            </p>
            <div className="flex justify-end gap-2">
              <button
                data-testid="frozen-move-cancel"
                onClick={() => setPendingFrozenMove(null)}
                className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3"
              >
                {t("tracker.cancel")}
              </button>
              <button
                data-testid="frozen-move-proceed"
                onClick={() => {
                  move.mutate({ id: pendingFrozenMove.id, stage: pendingFrozenMove.stage });
                  setPendingFrozenMove(null);
                }}
                className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:opacity-90"
              >
                {t("tracker.frozenMove.confirm", { stage: t(`tracker.stage.${pendingFrozenMove.stage}`) })}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {/* Detail modal */}
      {detail ? (
        <DetailModal
          app={detail}
          onClose={() => setDetailId(null)}
          onPriority={(p) => setPriority.mutate({ id: detail.id, priority: p })}
          onNotes={(notes) => updateApp.mutate({ id: detail.id, patch: { notes } })}
          onArchive={() => {
            archive.mutate(detail.id);
            setDetailId(null);
          }}
          onReturn={() => {
            returnToBoard.mutate(detail.id);
            setDetailId(null);
          }}
          onOpenPopup={(kind) => setPopup({ kind, appId: detail.id })}
        />
      ) : null}

      {/* 3-dot menu */}
      {menu ? (
        <CardMenu
          app={apps.find((a) => a.id === menu.id)!}
          anchor={menu.anchor}
          onClose={() => setMenu(null)}
          onGenerate={(label) => {
            setGuidance({ appId: menu.id, label });
            setMenu(null);
          }}
          onArchive={() => {
            archive.mutate(menu.id);
            setMenu(null);
          }}
          onReturn={() => {
            returnToBoard.mutate(menu.id);
            setMenu(null);
          }}
        />
      ) : null}

      {/* Resume/cover popups. For a MANUAL card with an uploaded doc of this
          kind, the modal shows it read-only (FR-TR manual-add) instead of the
          generate/tailor flow. */}
      {popup && popupApp && profile ? (
        <ResumeModal
          kind={popup.kind}
          profile={profile}
          application={popupApp}
          submittedDoc={
            popupApp.origin === "manual"
              ? popupApp.documents.find(
                  (d) => d.kind === (popup.kind === "cover" ? "cover_letter" : "tailored_resume"),
                )
              : undefined
          }
          onClose={() => setPopup(null)}
          onApprove={(markdown) => {
            // Persist the edited markdown + flip ready → approved (FR-RES-02).
            const kind = popup.kind === "cover" ? "cover" : "tailored";
            patchArtifact.mutate({ id: popupApp.id, kind, markdown, approved: true });
            setPopup(null);
          }}
          onSaveVariant={(markdown) => {
            // Persist an edit to an already-approved variant (FR-RES-02).
            const kind = popup.kind === "cover" ? "cover" : "tailored";
            patchArtifact.mutate({ id: popupApp.id, kind, markdown });
          }}
          onRegenerate={() => {
            setPopup(null);
            setGuidance({ appId: popupApp.id, label: popup.kind === "cover" ? "cover letter" : "tailored resume" });
          }}
        />
      ) : null}

      {/* Guidance / generation dialog */}
      {guidance ? (
        <GuidanceDialog
          label={guidance.label}
          onClose={() => setGuidance(null)}
          onGenerate={(text) =>
            // Per-artifact generation (US-TL-02/US-CL-01): the two modules are
            // independent — generating one must never trigger the other. The
            // freeform guidance (FR-TL-02) rides through to the Tailorer.
            genPacket.mutate({
              id: guidance.appId,
              resume: guidance.label !== "cover letter",
              cover: guidance.label === "cover letter",
              guidance: text,
            })
          }
        />
      ) : null}

      {/* Archive modal */}
      {showArchive ? (
        <ArchiveModal archived={archived} onClose={() => setShowArchive(false)} />
      ) : null}

      {/* "Add a job application" (FR-TR manual-add) — the Tracker sibling of the
          Job Board's Add-by-URL, for a job applied to outside the app. */}
      {showAddApp ? <AddApplicationModal onClose={() => setShowAddApp(false)} /> : null}

      {/* Applier companion panel (applier.md §8.2) — off the Apply slot. Bound
          to one Apply Run; Retry rebinds it to the fresh run (§8.3). Closing it
          never cancels the run. */}
      {applierPanel
        ? (() => {
            const a = apps.find((x) => x.id === applierPanel.appId);
            if (!a) return null;
            return (
              <ApplierPanel
                applicationId={a.id}
                runId={applierPanel.runId}
                role={a.job.title}
                company={a.job.company}
                onRebind={(newRunId) => setApplierPanel({ appId: a.id, runId: newRunId })}
                onClose={() => setApplierPanel(null)}
              />
            );
          })()
        : null}

      {/* Find-referrals popup (US-NW-09) — off the Referrals slot, restored
          2026-07-16. */}
      {referralsAppId
        ? (() => {
            const a = apps.find((x) => x.id === referralsAppId);
            if (!a) return null;
            return (
              <ReferralsModal
                jobId={a.job.id}
                jobTitle={a.job.title}
                company={a.job.company}
                applicationId={a.id}
                onClose={() => setReferralsAppId(null)}
              />
            );
          })()
        : null}
    </>
  );
}

// ─── Attached documents (FR-TR manual-add) — the resume/cover the user actually
// submitted for a manually-logged application, downloadable verbatim. ──────────

function AttachedDocuments({ docs }: { docs: ApplicationDocument[] }) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="space-y-1.5" data-testid="detail-documents">
      <div className="text-[12px] font-medium text-ink-2">{t("tracker.documents.submitted")}</div>
      <div className="flex flex-wrap gap-2">
        {docs.map((doc) => (
          <button
            key={doc.document_id}
            type="button"
            onClick={() =>
              downloadDocument(doc).catch((e: unknown) =>
                setError(e instanceof Error ? e.message : String(e)),
              )
            }
            data-testid={`doc-${doc.kind}`}
            title={t("tracker.documents.downloadTitle", { filename: doc.filename })}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] text-ink-2 hover:border-accent hover:text-ink"
          >
            <Icon name="file" size={13} strokeWidth={2} />
            {t(DOC_KIND_KEY[doc.kind])}
            <span className="max-w-[160px] truncate text-ink-4">· {doc.filename}</span>
          </button>
        ))}
      </div>
      {error ? <p className="text-[11.5px] text-bad">{error}</p> : null}
    </div>
  );
}

// ─── "Add a job application" (FR-TR manual-add) — the Tracker sibling of the Job
// Board's Add-by-URL. Same paste→preview→edit flow, plus the pipeline stage and
// the optional resume/cover the user submitted (stored content-addressed). ─────

// The upload formats the sidecar accepts (mirrors `documents.ALLOWED_TYPES`).
const DOC_ACCEPT = ".pdf,.doc,.docx,.txt,.md,.rtf";

function FilePicker({
  label,
  file,
  onPick,
  testid,
}: {
  label: string;
  file: File | null;
  onPick: (f: File | null) => void;
  testid: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2">
      <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] text-ink-2 hover:border-accent">
        <Icon name="file" size={13} strokeWidth={2} />
        {file ? t("tracker.documents.change") : label}
        <input
          type="file"
          accept={DOC_ACCEPT}
          data-testid={testid}
          className="hidden"
          onChange={(e) => onPick(e.target.files?.[0] ?? null)}
        />
      </label>
      {file ? (
        <span className="inline-flex min-w-0 items-center gap-1 text-[12px] text-ink-3">
          <span className="max-w-[180px] truncate">{file.name}</span>
          <button
            type="button"
            onClick={() => onPick(null)}
            className="text-ink-4 hover:text-bad"
            aria-label={t("tracker.documents.remove", { label })}
          >
            ×
          </button>
        </span>
      ) : (
        <span className="text-[11.5px] text-ink-4">{t("tracker.documents.optional")}</span>
      )}
    </div>
  );
}

function AddApplicationModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<"entry" | "fetching" | "editing">("entry");
  const [draft, setDraft] = useState<JobDraft | null>(null);
  const [stage, setStage] = useState<ManualApplicationInput["stage"]>("Applied");
  const [notes, setNotes] = useState("");
  const [resume, setResume] = useState<File | null>(null);
  const [cover, setCover] = useState<File | null>(null);
  const [tombstoned, setTombstoned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const preview = useJobPreview();
  const addApp = useAddManualApplication();

  function fetchDetails() {
    setPhase("fetching");
    setTombstoned(false);
    setError(null);
    preview.mutate(url, {
      onSuccess: (d) => {
        setDraft(d);
        setPhase("editing");
      },
      onError: (err) => {
        if (err instanceof JobTombstonedError) {
          setTombstoned(true);
          setPhase("entry");
          return;
        }
        // Other fetch failures: still let the user fill fields by hand
        // (rank-don't-gate escape hatch — they already applied, so we never
        // block logging it).
        setDraft({
          canonical_url: url,
          title: "",
          company: "",
          location: "",
          description: "",
          salary: "",
          source_adapter: "paste-url",
        });
        setPhase("editing");
      },
    });
  }

  function submit() {
    if (!draft) return;
    setError(null);
    addApp.mutate(
      {
        canonical_url: draft.canonical_url,
        title: draft.title,
        company: draft.company,
        location: draft.location,
        description: draft.description,
        salary: draft.salary,
        source_adapter: draft.source_adapter || "paste-url",
        stage,
        notes,
        resume,
        cover,
      },
      {
        onSuccess: () => onClose(),
        onError: (err) => setError(err instanceof Error ? err.message : String(err)),
      },
    );
  }

  function patch(fields: Partial<JobDraft>) {
    setDraft((d) => (d ? { ...d, ...fields } : d));
  }

  return (
    <Modal title={t("tracker.addApplication")} onClose={onClose} width={520}>
      {phase === "entry" ? (
        <form
          className="flex flex-col gap-3 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            fetchDetails();
          }}
        >
          <label className="text-[12.5px] text-ink-2">
            {t("tracker.addApp.intro")}
          </label>
          {tombstoned ? (
            <p
              data-testid="add-app-tombstoned"
              className="rounded-md border border-bad/40 bg-bad-wash px-3 py-2 text-[12px] text-bad"
            >
              {t("tracker.addApp.tombstoned")}
            </p>
          ) : null}
          <input
            type="url"
            required
            autoFocus
            data-testid="add-app-url-input"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setTombstoned(false);
            }}
            placeholder="https://company.com/careers/senior-engineer"
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
          />
          <div className="mt-1 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              {t("tracker.cancel")}
            </button>
            <button
              type="submit"
              data-testid="add-app-fetch-btn"
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
            >
              {t("tracker.addApp.fetch")}
            </button>
          </div>
        </form>
      ) : phase === "fetching" ? (
        <div className="grid place-items-center px-5 py-10 text-[13px] text-ink-3">
          <div className="flex items-center gap-2">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
            {t("tracker.addApp.fetching")}
          </div>
        </div>
      ) : (
        <form
          className="flex flex-col gap-3 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="text-[11.5px] text-ink-3">
            {url || t("tracker.addApp.noUrl")}{" "}
            <button
              type="button"
              onClick={() => setPhase("entry")}
              className="text-accent hover:underline"
            >
              {t("tracker.addApp.refetch")}
            </button>
          </div>
          <input
            value={draft?.title ?? ""}
            onChange={(e) => patch({ title: e.target.value })}
            placeholder={t("tracker.addApp.titlePlaceholder")}
            data-testid="add-app-title"
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <input
            value={draft?.company ?? ""}
            onChange={(e) => patch({ company: e.target.value })}
            placeholder={t("tracker.addApp.companyPlaceholder")}
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <input
            value={draft?.location ?? ""}
            onChange={(e) => patch({ location: e.target.value })}
            placeholder={t("tracker.addApp.locationPlaceholder")}
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <label className="flex items-center justify-between gap-3 text-[12.5px] text-ink-2">
            {t("tracker.addApp.stage")}
            <select
              value={stage}
              onChange={(e) => setStage(e.target.value as ManualApplicationInput["stage"])}
              data-testid="add-app-stage"
              className="rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink"
            >
              {MANUAL_STAGES.map((s) => (
                <option key={s} value={s}>
                  {t(`tracker.stage.${s}`)}
                </option>
              ))}
            </select>
          </label>
          <div className="space-y-2 rounded-md border border-border bg-surface-2 px-3 py-2.5">
            <div className="text-[12px] font-medium text-ink-2">
              {t("tracker.documents.used")}{" "}
              <span className="font-normal text-ink-4">{t("tracker.documents.optionalTag")}</span>
            </div>
            <FilePicker label={t("tracker.documents.attachResume")} file={resume} onPick={setResume} testid="add-app-resume" />
            <FilePicker label={t("tracker.documents.attachCover")} file={cover} onPick={setCover} testid="add-app-cover" />
          </div>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder={t("tracker.addApp.notesPlaceholder")}
            rows={3}
            className="resize-y rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          {error ? (
            <p data-testid="add-app-error" className="text-[12px] text-bad">
              {error}
            </p>
          ) : null}
          <div className="mt-1 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              {t("tracker.cancel")}
            </button>
            <button
              type="submit"
              disabled={addApp.isPending}
              data-testid="add-app-submit-btn"
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:opacity-60"
            >
              {addApp.isPending ? t("tracker.addApp.adding") : t("tracker.addApp.submit")}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}

// ─── Job detail block (US-TR-03) — the job-board fields on the Overview tab ───
// Everything the Job Board card/detail shows, so a tracked card is a full record
// without bouncing back to the board: logo, title, company · location · work-style,
// match score, the JD, and — most importantly — the canonical Job URL.

function JobDetail({ job }: { job: Job }) {
  const { t } = useTranslation();
  const tier = job.score ? scoreTier(job.score.score_0_100) : null;
  const meta = [job.company, job.location, workLabel(job.work_style)].filter(Boolean).join(" · ");
  return (
    <div className="space-y-3" data-testid="detail-job-info">
      <div className="flex items-start gap-3">
        <span className="inline-grid h-10 w-10 shrink-0 place-items-center rounded-md bg-surface-3 font-mono text-[13px] font-semibold text-ink-2">
          {initials(job.company)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold leading-snug text-ink">{job.title}</div>
          <div className="truncate text-[12px] text-ink-3">{meta}</div>
        </div>
        {job.score ? (
          <span
            data-testid="detail-match-score"
            title={t("tracker.jobDetail.matchScoreTitle")}
            className={`inline-grid h-10 w-10 shrink-0 place-items-center rounded-full border font-mono text-[13px] font-semibold ${tier?.ring} ${tier?.text}`}
          >
            {job.score.score_0_100}
          </span>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-3 text-[12px]">
        <a
          href={job.canonical_url}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="detail-job-url"
          className="inline-flex items-center gap-1 font-medium text-accent hover:underline"
        >
          {t("tracker.jobDetail.openPosting")}
        </a>
        {job.salary ? <span className="text-ink-3">{job.salary}</span> : null}
      </div>
      {job.description ? (
        <details className="rounded-md border border-border bg-surface-2" data-testid="detail-jd">
          <summary className="cursor-pointer px-3 py-2 text-[12px] font-medium text-ink-2">
            {t("tracker.jobDetail.jobDescription")}
          </summary>
          <div className="max-h-64 overflow-y-auto border-t border-border px-3 py-2 text-[12.5px] leading-relaxed text-ink-2">
            <Markdown md={job.description} />
          </div>
        </details>
      ) : null}
    </div>
  );
}

// ─── Detail modal (US-TR-03/04/10 + Applier screenshot) ──────────────────────

function DetailModal({
  app,
  onClose,
  onPriority,
  onNotes,
  onArchive,
  onReturn,
  onOpenPopup,
}: {
  app: Application;
  onClose: () => void;
  onPriority: (p: Priority) => void;
  onNotes: (notes: string) => void;
  onArchive: () => void;
  onReturn: () => void;
  onOpenPopup: (kind: ResumeModalKind) => void;
}) {
  // Networking tab restored 2026-07-16 (the referral-outreach backend now
  // exists) — shown only when the LinkedIn toggle is on (US-TR-03 / FR-TR-03),
  // same gate the prior repo used.
  type Tab = "Overview" | "Notes" | "Scoring" | "Activity" | "Networking";
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("Overview");
  const [notes, setNotes] = useState(app.notes);
  const linkedInOn = Boolean(useLinkedInSession().data?.enabled);
  const activity = useApplicationActivity(app.id);
  const networking = useApplicationNetworking(tab === "Networking" ? app.id : null);
  // Activity sits last (maintainer, 2026-07-11) — it's the audit trail, not
  // the working surface.
  const tabs: Tab[] = [
    "Overview",
    "Notes",
    "Scoring",
    ...(linkedInOn ? (["Networking"] as const) : []),
    "Activity",
  ];

  return (
    <Modal title={`${app.job.title} · ${app.job.company}`} onClose={onClose} width={640}>
      <div className="flex items-center gap-1 border-b border-border px-5">
        {tabs.map((tb) => (
          <button
            key={tb}
            onClick={() => setTab(tb)}
            className={
              "border-b-2 px-3 py-2 text-[12.5px] " +
              (tab === tb ? "border-accent font-medium text-ink" : "border-transparent text-ink-3 hover:text-ink")
            }
          >
            {t(`tracker.detail.tab.${tb}`)}
          </button>
        ))}
      </div>
      <div className="px-5 py-4">
        {tab === "Overview" ? (
          <div className="space-y-4">
            <JobDetail job={app.job} />
            {app.documents.length > 0 ? <AttachedDocuments docs={app.documents} /> : null}
            <div className="flex items-center gap-3">
              <span className="text-[12px] text-ink-3">{t("tracker.detail.priority")}</span>
              <select
                value={app.priority}
                onChange={(e) => onPriority(e.target.value as Priority)}
                data-testid="priority-select"
                className="rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink"
              >
                {(["P0", "P1", "P2", "P3"] as Priority[]).map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <span
                className="ml-auto font-mono text-[11px] text-ink-4"
                data-testid="app-ref"
                title={t("tracker.detail.appRefTitle")}
              >
                {"#" + app.id.replace(/-/g, "").slice(-6).toUpperCase()}
              </span>
              <span className="text-[12px] text-ink-3">
                {t("tracker.detail.stageLine", { stage: t(`tracker.stage.${app.stage}`) })}
              </span>
            </div>
            {app.posting_closed || app.job.board_state === "expired" ? (
              <div
                className="rounded-md border border-bad/40 bg-bad-wash px-3 py-2 text-[12px] text-bad"
                data-testid="posting-closed-note"
              >
                {t("tracker.detail.postingClosed")}
              </div>
            ) : null}
            <div className="flex gap-2">
              <button
                onClick={() => onOpenPopup("tailored")}
                className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3"
              >
                {app.packet_resume_state === "ready" || app.packet_resume_state === "approved"
                  ? t("tracker.detail.viewResume")
                  : app.packet_resume_state === "generating"
                    ? t("tracker.detail.generatingResume")
                    : t("tracker.detail.generateResume")}
              </button>
              <button
                onClick={() => onOpenPopup("cover")}
                className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3"
              >
                {app.packet_cover_state === "ready" || app.packet_cover_state === "approved"
                  ? t("tracker.detail.viewCover")
                  : app.packet_cover_state === "generating"
                    ? t("tracker.detail.generatingCover")
                    : t("tracker.detail.generateCover")}
              </button>
            </div>
            {/* REMOVED: Apply button, Applier run summary, and Applier preview
                screenshot (no Applier surface on this sidecar yet). */}
            <div className="flex gap-2 border-t border-border pt-3">
              {app.stage === "Saved" ? (
                <button
                  onClick={onReturn}
                  className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3"
                >
                  {t("tracker.moveToDiscover")}
                </button>
              ) : null}
              <button
                onClick={onArchive}
                className="ml-auto rounded-md border border-bad/40 px-3 py-1.5 text-[12.5px] text-bad hover:bg-bad-wash"
              >
                {t("tracker.archive")}
              </button>
            </div>
          </div>
        ) : tab === "Notes" ? (
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => onNotes(notes)}
            data-testid="notes-editor"
            rows={8}
            placeholder={t("tracker.detail.notesPlaceholder")}
            className="w-full resize-none rounded-md border border-border bg-surface p-3 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
          />
        ) : tab === "Scoring" ? (
          app.job.score ? (
            <div>
              <div className="mb-2 text-[13px] font-semibold text-ink">
                {t("tracker.detail.matchScore", { score: app.job.score.score_0_100 })}
              </div>
              <ul className="mb-3 space-y-1 text-[12px] text-ink-2">
                {app.job.score.reasons.map((r, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="mt-1 size-1 shrink-0 rounded-full bg-accent" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
              <Markdown md={app.job.score.breakdown_md} className="text-[11.5px]" />
            </div>
          ) : (
            <p className="text-[12.5px] text-ink-3">{t("tracker.detail.scoringPending")}</p>
          )
        ) : tab === "Activity" ? (
          // Real Activity log (US-TR-03 / FR-TR-03) — composed server-side from
          // the operations ledger + outreach log, not synthesized client-side.
          <ul className="space-y-2 text-[12px] text-ink-2" data-testid="activity-log">
            {activity.isLoading ? (
              <li className="text-ink-3">{t("tracker.detail.loadingActivity")}</li>
            ) : (activity.data ?? []).length === 0 ? (
              <li className="text-ink-3">{t("tracker.detail.noActivity")}</li>
            ) : (
              // Reverse chronological — newest first (maintainer, 2026-07-11).
              [...(activity.data ?? [])]
                .sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""))
                .map((e, i) => (
                <li key={i} className="flex items-start gap-2" data-testid="activity-entry">
                  <span
                    className={
                      "mt-1 size-1.5 shrink-0 rounded-full " +
                      (e.state === "failed" ? "bg-bad" : "bg-accent")
                    }
                  />
                  <span className="flex-1">{e.label}</span>
                  {e.at ? (
                    <span className="font-mono text-[10.5px] text-ink-4">
                      {formatActivityAt(e.at)}
                    </span>
                  ) : null}
                </li>
              ))
            )}
          </ul>
        ) : (
          // Networking tab (US-TR-03) — the role's referral contacts + statuses.
          // Restored 2026-07-16.
          <div data-testid="networking-tab">
            {networking.isLoading ? (
              <p className="text-[12.5px] text-ink-3">{t("tracker.detail.loadingContacts")}</p>
            ) : (networking.data ?? []).length === 0 ? (
              <p className="text-[12.5px] text-ink-3">
                {t("tracker.detail.noContacts")}
              </p>
            ) : (
              <ul className="space-y-2">
                {(networking.data ?? []).map((c) => (
                  <li
                    key={c.contact_id}
                    data-testid="networking-contact"
                    className="rounded-md border border-border px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[12.5px] font-medium text-ink">{c.name || t("tracker.detail.unknown")}</span>
                      <span className="rounded-full border border-border-2 bg-surface-2 px-1.5 py-0.5 text-[10px] text-ink-3">
                        {c.connection_status}
                      </span>
                    </div>
                    <div className="text-[11px] text-ink-3">
                      {[c.role, c.company].filter(Boolean).join(" · ")}
                    </div>
                    {c.last_message ? (
                      <div className="mt-1 truncate text-[11px] text-ink-4">
                        {t("tracker.detail.lastMessage", { message: c.last_message })}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

function CardMenu({
  app,
  anchor,
  onClose,
  onGenerate,
  onArchive,
  onReturn,
}: {
  app: Application;
  anchor: DOMRect;
  onClose: () => void;
  onGenerate: (label: string) => void;
  onArchive: () => void;
  onReturn: () => void;
}) {
  const { t } = useTranslation();
  const canGen = app.packet_state === "none" || app.packet_state === "failed";
  const canRegen = app.packet_state === "ready" || app.packet_state === "approved";
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // Anchored popover, not a modal (maintainer 2026-07-22 #5): opens flush to
  // the ⋮'s right — over the neighbouring column, never its own card — and
  // clamps to the viewport. The dim layer below sits under the open card
  // (z-50) so only the card + menu read highlighted.
  const W = 232;
  const left = Math.min(anchor.right + 6, window.innerWidth - W - 8);
  const top = Math.min(anchor.top - 4, window.innerHeight - 260);
  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/20"
        onClick={onClose}
        data-testid="card-menu-backdrop"
      />
      <div
        role="menu"
        data-testid="card-menu"
        style={{ left, top, width: W }}
        className="fixed z-50 flex flex-col rounded-lg border border-border bg-surface p-1.5 text-[13px] shadow-xl"
      >
        {app.stage === "Saved" ? (
          <button onClick={onReturn} className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3">
            {t("tracker.moveToDiscover")}
          </button>
        ) : null}
        {canGen ? (
          <>
            <button
              onClick={() => onGenerate("tailored resume")}
              className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3"
            >
              {t("tracker.menu.generateResume")}
            </button>
            <button
              onClick={() => onGenerate("cover letter")}
              className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3"
            >
              {t("tracker.menu.generateCover")}
            </button>
          </>
        ) : null}
        {canRegen ? (
          <>
            <button
              onClick={() => onGenerate("tailored resume")}
              className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3"
            >
              {t("tracker.menu.regenResume")}
            </button>
            <button
              onClick={() => onGenerate("cover letter")}
              className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3"
            >
              {t("tracker.menu.regenCover")}
            </button>
          </>
        ) : null}
        <button onClick={onArchive} className="rounded px-3 py-2 text-left text-bad hover:bg-bad-wash">
          {t("tracker.archive")}
        </button>
      </div>
    </>
  );
}

function ArchiveModal({ archived, onClose }: { archived: Application[]; onClose: () => void }) {
  const { t } = useTranslation();
  const unarchive = useUnarchiveApplication();
  return (
    <Modal title={t("tracker.deletedApplications")} onClose={onClose} width={520}>
      <div data-testid="deleted-applications-modal" className="px-5 py-4">
        {archived.length === 0 ? (
          <p className="text-[13px] text-ink-3">{t("tracker.archiveModal.empty")}</p>
        ) : (
          <ul className="space-y-2">
            {archived.map((a) => (
              <li key={a.id} className="flex items-center gap-3 rounded-md border border-border px-3 py-2">
                <div className="grid h-8 w-8 place-items-center rounded bg-surface-2 text-[11px] font-semibold text-ink-2">
                  {initials(a.job.company)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium text-ink">{a.job.title}</div>
                  <div className="text-[11px] text-ink-3">{a.job.company} · {t("tracker.archiveModal.deletedRecently")}</div>
                </div>
                <button
                  onClick={() => unarchive.mutate(a.id)}
                  className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                >
                  {t("tracker.archiveModal.restore")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
