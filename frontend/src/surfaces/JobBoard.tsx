// Job Board (US-JB-01..07, 09, 10, 11, 12) — two-column scored feed + JD detail,
// filters/sort, board search (list + deep all-text), resizable persisted split,
// Save/Remove, Add a job, Trash, source filter, master-resume popup.
// Ports design/prototype/prototype-modal/jobs.html.

import { useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import {
  useAddJobByUrl,
  useDiscoverReferrals,
  useLinkedInSession,
  useBoard,
  useEmptyTrash,
  useSaveJob,
  useSettings,
  useTombstoneJob,
  useTrash,
  useTrashJob,
} from "../api/queries";
import { type BoardFilters } from "../api/types";
import { HeaderAddButton, HeaderDeletedButton } from "../shell/HeaderAddButton";
import { useDebounced } from "../shell/useDebounced";
import { Icon } from "../shell/icons";
import { Chip, FilterBar, FilterGroup, FilterSep, SearchBox } from "../shell/FilterRow";
import { MasterResumeLauncher } from "../shell/MasterResumeLauncher";
import { AddJobModal } from "./jobs/AddJobModal";
import { ScanProgressPill } from "./ScanProgressPill";
import { relativeTime, shortAgo } from "./jobFormat";
import { BoardEmptyState } from "./jobs/BoardEmptyState";
import { FinderPrefsModal } from "./jobs/FinderPrefsModal";
import { JobDetail } from "./jobs/JobDetail";
import { JobRow } from "./jobs/JobRow";
import { TrashModal } from "./jobs/TrashModal";

// Every job always carries at least a keyword score (the instant on-device
// floor), so "Pending"/"Failed" are gone — the filter is now which scorer
// produced the displayed score (maintainer 2026-07-22).
type StatusFilter = "ALL" | "MODEL" | "KEYWORD";
type WorkStyleFilter = "ALL" | "REMOTE" | "HYBRID" | "ONSITE" | "REMOTE_FRIENDLY";
type SortMode = "match" | "recency";
/** The URL axis. "" is All — the server reads it as no filter (section 9.3). */
type LinkFilter = "" | "has" | "none";

const SPLIT_KEY = "fyj-job-board-split";

export function JobBoard() {
  const { t } = useTranslation();
  // Board search (FR-JB-13, consolidated 2026-07-22): ONE bar next to Sort,
  // deep all-text match (title/company/location + JD + score texts), matches
  // highlighted in the list and the open JD. Server-side (the feed is
  // paginated — filtering loaded pages client-side would miss matches on
  // unloaded pages), debounced per keystroke.
  const [textSearch, setTextSearch] = useState("");
  const textQ = useDebounced(textSearch.trim(), 250);
  // The URL axis filters server-side for the same reason the search does.
  const [linkFilter, setLinkFilter] = useState<LinkFilter>("");
  const [status, setStatus] = useState<StatusFilter>("ALL");
  const [ws, setWs] = useState<WorkStyleFilter>("ALL");
  const [sort, setSort] = useState<SortMode>("match");
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);
  const [salaryMin, setSalaryMin] = useState(0);
  const [posted, setPosted] = useState<number>(0); // 0=all
  // The chips filter server-side too, for the same reason (audit finding 6).
  const filters = useMemo<BoardFilters>(
    () => ({
      source: sourceFilter ?? "",
      work_style: ws === "ALL" ? "" : ws,
      method: status === "MODEL" ? "model" : status === "KEYWORD" ? "keyword" : "",
      posted_days: posted,
      salary_min: salaryMin,
      sort,
    }),
    [sourceFilter, ws, status, posted, salaryMin, sort],
  );
  const board = useBoard("", textQ, linkFilter, filters);
  const { data: trashed = [] } = useTrash();
  const saveJob = useSaveJob();
  const trashJob = useTrashJob();
  const emptyTrash = useEmptyTrash();
  const tombstoneJob = useTombstoneJob();
  const addByUrl = useAddJobByUrl();
  const { data: settings } = useSettings();
  const session = useLinkedInSession();
  const discoverReferrals = useDiscoverReferrals();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [split, setSplit] = useState<number>(() => {
    const s = Number(localStorage.getItem(SPLIT_KEY));
    return s >= 20 && s <= 80 ? s : 32;
  });
  const [showAdd, setShowAdd] = useState(false);
  const [showTrash, setShowTrash] = useState(false);
  const [showPrefs, setShowPrefs] = useState(false);
  const dragging = useRef(false);

  // The board feed is served paginated + saved-excluded server-side (FR-JB-02);
  // pages accumulate for infinite scroll. `meta` carries the total + last-scan
  // status for the header and the explained empty state (FR-JB-10).
  const boardJobs = useMemo(
    () => board.data?.pages.flatMap((p) => p.jobs) ?? [],
    [board.data],
  );
  const meta = board.data?.pages[0];

  // The server has already filtered and sorted every row (audit finding 6).
  const visible = boardJobs;

  const selected = visible.find((j) => j.id === selectedId) ?? visible[0] ?? null;

  // Virtualized list (F-M4): only the on-screen window of rows hits the DOM —
  // the feed contemplates 1000+ jobs and infinite scroll appends unbounded.
  // The trailing "Load more" button rides along as one extra virtual row so it
  // stays exactly where the plain list put it. Row heights vary (wrapping skill
  // chips, expired label), so measureElement corrects the estimate per row.
  const listRef = useRef<HTMLDivElement | null>(null);
  const rowCount = visible.length + (board.hasNextPage ? 1 : 0);
  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => listRef.current,
    estimateSize: () => 96,
    overscan: 10,
  });

  function onDrag(e: React.MouseEvent) {
    dragging.current = true;
    const startX = e.clientX;
    const startSplit = split;
    const width = (e.currentTarget.parentElement?.clientWidth ?? 1000) - 76;
    const move = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const delta = ((ev.clientX - startX) / width) * 100;
      const next = Math.min(80, Math.max(20, startSplit + delta));
      setSplit(next);
    };
    const up = () => {
      dragging.current = false;
      localStorage.setItem(SPLIT_KEY, String(Math.round(split)));
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  return (
    <>
      {/* Topbar */}
      <header className="flex min-h-[48px] items-center border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">{t("nav.jobBoard")}</h1>
        <div className="ml-auto flex items-center gap-3 py-1.5">
          {/* Board-level Rescan status (observed-issue #2) — renders only while a
              scan/scoring cycle is live (or just finished); nothing otherwise. */}
          <ScanProgressPill />
          {/* Finder prefs (Job-Board-only) sits LEFT of Master Resume so Master
              Resume lands immediately before the shared Deleted+Add cluster —
              the one spot it can share pixel-for-pixel with the Applications and
              Networking tabs (maintainer 2026-07-28 swap). */}
          {/* Disabled until the prefs land: the modal is seeded from them, and
              a control that renders is a control that works (section 7.4). */}
          <button
            onClick={() => setShowPrefs(true)}
            data-testid="finder-prefs"
            disabled={!settings}
            title={t("jobBoard.header.finderPrefsTitle")}
            className="inline-flex h-[30px] shrink-0 items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink disabled:opacity-50"
          >
            <Icon name="settings" size={14} strokeWidth={2} />
            {t("jobBoard.header.finderPrefs")}
          </button>
          <MasterResumeLauncher />
          <HeaderDeletedButton
            label={t("jobBoard.header.deletedJobs")}
            count={trashed.length}
            onClick={() => setShowTrash(true)}
            testid="trash-btn"
          />
          <HeaderAddButton
            label={t("jobBoard.header.addJob")}
            onClick={() => setShowAdd(true)}
            testid="add-job"
          />
        </div>
      </header>

      {/* Filter row — right-aligned like the Applications/Networking FilterBar,
          so the trailing Search box lands on the same edge in every tab
          (maintainer 2026-07-24 #4). */}
      <FilterBar testid="v2-filters">
        {/* Work style chip group (US-JB-02) — first, per the prototype filter row */}
        <FilterGroup label={t("jobBoard.filters.workStyle")} id="filter-ws">
          {(
            [
              ["ALL", "jobBoard.filters.all"],
              ["REMOTE", "jobBoard.filters.remote"],
              ["HYBRID", "jobBoard.filters.hybrid"],
              ["ONSITE", "jobBoard.filters.onsite"],
              ["REMOTE_FRIENDLY", "jobBoard.filters.remoteFriendly"],
            ] as [WorkStyleFilter, string][]
          ).map(([v, l]) => (
            <Chip key={v} active={ws === v} onClick={() => setWs(v)}>
              {t(l)}
            </Chip>
          ))}
        </FilterGroup>
        <FilterSep />
        <FilterGroup label={t("jobBoard.filters.posted")}>
          {[
            [0, "jobBoard.filters.anyTime"],
            [1, "jobBoard.filters.last24h"],
            [7, "jobBoard.filters.last7d"],
            [14, "jobBoard.filters.last14d"],
            [30, "jobBoard.filters.last30d"],
          ].map(([v, l]) => (
            <Chip key={v} active={posted === v} onClick={() => setPosted(v as number)}>
              {t(l as string)}
            </Chip>
          ))}
        </FilterGroup>
        <FilterSep />
        <FilterGroup label={t("jobBoard.filters.minSalary")}>
          {[
            [0, "jobBoard.filters.any"],
            [50000, "jobBoard.filters.salary50k"],
            [100000, "jobBoard.filters.salary100k"],
            [150000, "jobBoard.filters.salary150k"],
          ].map(([v, l]) => (
            <Chip key={v} active={salaryMin === v} onClick={() => setSalaryMin(v as number)}>
              {t(l as string)}
            </Chip>
          ))}
        </FilterGroup>
        <FilterSep />
        <FilterGroup label={t("jobBoard.filters.status")} id="filter-status">
          {(
            [
              ["ALL", "jobBoard.filters.all"],
              ["MODEL", "jobBoard.filters.modelScored"],
              ["KEYWORD", "jobBoard.filters.keywordsScored"],
            ] as [StatusFilter, string][]
          ).map(([s, label]) => (
            <Chip key={s} active={status === s} onClick={() => setStatus(s)}>
              {t(label)}
            </Chip>
          ))}
        </FilterGroup>
        <FilterSep />
        {/* The URL axis (url-less-jobs.md section 9.3). The source chip on each
            card covers the other one, scanned versus typed in, and the 2
            deliberately disagree on a promoted job. */}
        <FilterGroup label={t("jobBoard.filters.link")} id="filter-link">
          {(
            [
              ["", "jobBoard.filters.all"],
              ["has", "jobBoard.filters.linkHas"],
              ["none", "jobBoard.filters.linkNone"],
            ] as [LinkFilter, string][]
          ).map(([v, label]) => (
            <Chip key={v || "all"} active={linkFilter === v} onClick={() => setLinkFilter(v)}>
              {t(label)}
            </Chip>
          ))}
        </FilterGroup>
        <FilterSep />
        <FilterGroup label={t("jobBoard.filters.sort")}>
          <div
            data-testid="sort-toggle"
            className="inline-flex items-center rounded-full border border-border-2 bg-surface p-0.5"
          >
            {(["match", "recency"] as SortMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setSort(m)}
                className={
                  "h-6 rounded-full px-2.5 text-[11.5px] font-medium " +
                  (sort === m ? "bg-accent text-white" : "text-ink-2 hover:bg-surface-3")
                }
              >
                {m === "match" ? t("jobBoard.filters.sortMatchScore") : t("jobBoard.filters.sortRecency")}
              </button>
            ))}
          </div>
        </FilterGroup>
        <FilterSep />
        {/* THE board search (FR-JB-13): sits right after SORT, behind the same
            "|" separator as the other groups. A SMALL flex-basis + min-width
            means it shrinks to fit the space the filter groups leave — down to
            icon + "Search" (~88px) — and only wraps when even that can't fit.
            The shorter "All"/"Any" labels free up room so it stays inline
            (maintainer 2026-07-22). */}
        <SearchBox
          value={textSearch}
          onChange={setTextSearch}
          placeholder={t("jobBoard.filters.searchPlaceholder")}
          testid="board-text-search"
        />
      </FilterBar>

      {/* Split */}
      <div className="flex min-h-0 flex-1" data-testid="v2-split-host">
        <div className="flex min-w-0 flex-col border-r border-border" style={{ flexBasis: `${split}%` }}>
          <div className="flex items-center justify-between border-b border-border px-4 py-2 text-[12px] text-ink-3">
            <span data-testid="job-list-count">
              {/* Live total from the server, not the loaded/filtered count (FR-JB-02);
                  real "last refresh" from the last successful scan (FR-JB-10). */}
              {t("jobBoard.list.jobsCount", { count: meta?.total ?? visible.length })}{" "}
              <span className="text-ink-3/80" data-testid="last-refresh">
                {t("jobBoard.list.lastRefresh", { ago: relativeTime(shortAgo(meta?.last_scan_at)) })}
              </span>
            </span>
          </div>
          <div
            ref={listRef}
            role="listbox"
            aria-label={t("jobBoard.list.jobsAria")}
            className="flex-1 overflow-y-auto"
            onScroll={(e) => {
              // Infinite scroll (FR-JB-02): fetch the next page near the bottom.
              const el = e.currentTarget;
              if (
                board.hasNextPage &&
                !board.isFetchingNextPage &&
                el.scrollTop + el.clientHeight >= el.scrollHeight - 120
              ) {
                void board.fetchNextPage();
              }
            }}
          >
            {visible.length === 0 ? (
              <BoardEmptyState
                meta={meta}
                loading={board.isLoading}
                // A server-side search miss returns zero rows with scan_status
                // "idle" — that's a filter miss, never an empty scrape (FR-JB-13).
                filteredOut={boardJobs.length > 0 || Boolean(textQ) || Boolean(linkFilter)}
                onAddJob={() => setShowAdd(true)}
              />
            ) : (
              <div
                className="relative w-full"
                style={{ height: rowVirtualizer.getTotalSize() }}
              >
                {rowVirtualizer.getVirtualItems().map((vi) => {
                  const j = visible[vi.index];
                  return (
                    <div
                      key={j?.id ?? "board-load-more"}
                      data-index={vi.index}
                      ref={rowVirtualizer.measureElement}
                      className="absolute left-0 top-0 w-full"
                      style={{ transform: `translateY(${vi.start}px)` }}
                    >
                      {j ? (
                        <JobRow
                          job={j}
                          selected={selected?.id === j.id}
                          onSelect={setSelectedId}
                          q={textQ}
                          onLinkFilter={() => setLinkFilter("none")}
                        />
                      ) : (
                        // The extra virtual row past the loaded feed.
                        <div className="grid place-items-center py-3">
                          <button
                            data-testid="board-load-more"
                            onClick={() => void board.fetchNextPage()}
                            disabled={board.isFetchingNextPage}
                            className="rounded-full border border-border-2 bg-surface px-3 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3 disabled:opacity-50"
                          >
                            {board.isFetchingNextPage ? t("jobBoard.list.loading") : t("jobBoard.list.loadMore")}
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
        <div
          role="separator"
          aria-orientation="vertical"
          data-testid="resize-handle"
          onMouseDown={onDrag}
          className="group flex w-1.5 shrink-0 cursor-col-resize items-center justify-center hover:bg-accent-wash resize-handle"
        >
          <span className="block h-8 w-px bg-border-2 group-hover:bg-accent" />
        </div>
        <div className="min-w-0 flex-1 overflow-hidden">
          <JobDetail
            job={selected}
            onSave={(j, gen) =>
              saveJob.mutate({
                id: j.id,
                saved: true,
                generate_resume: gen.resume,
                generate_cover: gen.cover,
              })
            }
            onRemove={(j) => {
              trashJob.mutate({ id: j.id, trashed: true });
              setSelectedId(null);
            }}
            onUnexpire={(j) => trashJob.mutate({ id: j.id, trashed: false })}
            sourceFilter={sourceFilter}
            onToggleSource={(a) => setSourceFilter((cur) => (cur === a ? null : a))}
            networkingEnabled={Boolean(session.data?.enabled)}
            onFindReferrals={(j) => discoverReferrals.mutate(j.id)}
            searchQ={textQ}
            packetDefaults={{
              resume: settings?.auto_resume_on_save ?? true,
              cl: settings?.auto_cover_on_save ?? true,
              refs: settings?.auto_referrals_on_save ?? false,
            }}
          />
        </div>
      </div>

      {/* Modals */}
      {showAdd ? (
        <AddJobModal onClose={() => setShowAdd(false)} onAdd={(draft) => addByUrl.mutateAsync(draft)} />
      ) : null}
      {showTrash ? (
        <TrashModal
          trashed={trashed}
          onClose={() => setShowTrash(false)}
          onUndo={(id) => trashJob.mutate({ id, trashed: false })}
          onDeleteForever={(id) => tombstoneJob.mutate(id)}
          onEmpty={() => emptyTrash.mutate()}
        />
      ) : null}
      {showPrefs && settings ? (
        <FinderPrefsModal
          prefs={settings.job_prefs}
          onClose={() => setShowPrefs(false)}
        />
      ) : null}
    </>
  );
}

