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
//
// Split 2026-07-25 (F-M6): Card, DetailModal, CardMenu, ArchiveModal, and
// AddApplicationModal live in ./tracker/*; this file keeps the board state.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useApplications,
  useArchived,
  useArchiveApplication,
  useGeneratePacket,
  useMoveApplication,
  usePatchArtifact,
  useProfile,
  useReturnToBoard,
  useSetPriority,
  useStartApply,
  useUpdateApplication,
} from "../api/queries";
import { HeaderAddButton, HeaderDeletedButton } from "../shell/HeaderAddButton";
import type { Application, Priority, Stage } from "../api/types";
import { STAGES } from "../api/types";
import { ApplierPanel } from "../popups/ApplierPanel";
import { GuidanceDialog } from "../popups/GuidanceDialog";
import { ReferralsModal } from "../popups/ReferralsModal";
import { ResumeModal, type ResumeModalKind } from "../popups/ResumeModal";
import { Chip, FilterBar, FilterGroup, FilterSep, SearchBox } from "../shell/FilterRow";
import { Modal } from "../shell/Modal";
import { AddApplicationModal } from "./tracker/AddApplicationModal";
import { ArchiveModal } from "./tracker/ArchiveModal";
import { Card } from "./tracker/Card";
import { CardMenu } from "./tracker/CardMenu";
import { DetailModal } from "./tracker/DetailModal";

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
  // Stable (mutateAsync is referentially stable in TanStack Query v5) so the
  // memoized Card's onSlot callback stays stable too.
  const onApplyClick = useCallback(
    async (app: Application) => {
      if (app.apply_run_id) {
        setApplierPanel({ appId: app.id, runId: app.apply_run_id });
        return;
      }
      // Failure is logged + bannered by the global MutationCache hook — this
      // catch only stops the unhandled rejection (2026-07-24 audit).
      try {
        const run = await Promise.resolve(startApply.mutateAsync({ applicationId: app.id }));
        if (run) setApplierPanel({ appId: app.id, runId: run.id });
      } catch {
        /* surfaced globally */
      }
    },
    [startApply.mutateAsync],
  );

  // Stable per-card callbacks so the memoized Card only re-renders when its own
  // props change — not on every search keystroke or menu open.
  const handleOpen = useCallback((id: string) => {
    // A card with its menu open stays clickable (it lifts above the backdrop)
    // — opening the detail dismisses the popover instead of leaving it
    // stranded beneath.
    setDetailId(id);
    setMenu(null);
  }, []);
  const handleDragStart = useCallback((id: string) => setDragId(id), []);
  const handleDragEnd = useCallback(() => setDragId(null), []);
  const handleSlot = useCallback(
    (app: Application, kind: ResumeModalKind | "refs" | "apply") => {
      if (kind === "refs") {
        // Open the find-referrals popup (US-NW-09). It handles connected /
        // drafts-only / no-session states internally.
        setReferralsAppId(app.id);
        return;
      }
      if (kind === "apply") {
        void onApplyClick(app);
        return;
      }
      setPopup({ kind, appId: app.id });
    },
    [onApplyClick],
  );
  const handleMenu = useCallback((id: string, anchor: DOMRect) => setMenu({ id, anchor }), []);

  const detail = apps.find((a) => a.id === detailId) ?? null;
  const popupApp = popup ? apps.find((a) => a.id === popup.appId) : undefined;
  // The ⋮ menu's card, resolved defensively: the list can refetch WITHOUT the
  // card while the menu is open (archived via the detail modal — the open card
  // sits above the menu backdrop and stays clickable — or from another window).
  // A stale id must degrade to "menu closed", never render and crash
  // (2026-07-24 customer-reported crash: `app.packet_state` of undefined).
  const menuApp = menu ? (apps.find((a) => a.id === menu.id) ?? null) : null;
  useEffect(() => {
    if (menu && !menuApp) setMenu(null);
  }, [menu, menuApp]);

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
                      onOpen={handleOpen}
                      onDragStart={handleDragStart}
                      onDragEnd={handleDragEnd}
                      onSlot={handleSlot}
                      onMenu={handleMenu}
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
      {menu && menuApp ? (
        <CardMenu
          app={menuApp}
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
