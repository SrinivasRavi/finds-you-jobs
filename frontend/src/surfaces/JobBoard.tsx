// Job Board (US-JB-01..07, 09, 10, 11, 12) — two-column scored feed + JD detail,
// filters/sort, board search (list + deep all-text), resizable persisted split,
// Save/Remove, Add-by-URL, Trash, source filter, master-resume popup.
// Ports design/prototype/prototype-modal/jobs.html.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queries";

import {
  useAddJobByUrl,
  useDiscoverReferrals,
  useLinkedInSession,
  useUpdateProfile,
  useBoard,
  useEmptyTrash,
  useJobPreview,
  useProfile,
  useSaveJob,
  useSettings,
  useTombstoneJob,
  useTrash,
  useTrashJob,
  useTriggerScan,
  useUnwatchCompany,
  useWatchCompany,
  useWatchlist,
} from "../api/queries";
import { JobTombstonedError, type BoardPage, type Job, type JobDraft } from "../api/types";
import { Icon } from "../shell/icons";
import { Modal } from "../shell/Modal";
import { Markdown } from "../shell/Markdown";
import { ResumeModal } from "../popups/ResumeModal";
import {
  firstHeading,
  initials,
  salaryFloor,
  scoreTier,
  shortAgo,
  sourceClasses,
  timeAgo,
  workLabel,
} from "./jobFormat";

type StatusFilter = "ALL" | "SCORED" | "PENDING" | "FAILED";
type WorkStyleFilter = "ALL" | "REMOTE" | "HYBRID" | "ONSITE" | "REMOTE_FRIENDLY";
type SortMode = "match" | "recency";

const SPLIT_KEY = "fyj-job-board-split";

/** Debounce a fast-changing value (the search inputs) so each keystroke doesn't
 *  fire a board refetch — the search runs server-side (FR-JB-13). */
function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

/** One search input with a clear (×) button — clearing restores the unfiltered
 *  feed (FR-JB-13). Used for both the list search and the deep all-text search. */
function SearchBox({
  value,
  onChange,
  placeholder,
  testid,
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  testid: string;
  className?: string;
}) {
  return (
    <div
      className={
        "flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-2 focus-within:border-accent " +
        className
      }
    >
      <Icon name="search" size={13} strokeWidth={2} className="shrink-0 text-ink-4" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        data-testid={testid}
        className="min-w-0 flex-1 bg-transparent text-[12px] text-ink placeholder:text-ink-4 focus:outline-none"
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange("")}
          data-testid={`${testid}-clear`}
          aria-label="Clear search"
          className="shrink-0 text-ink-3 hover:text-ink"
        >
          ×
        </button>
      ) : null}
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "fchip h-7 rounded-full border px-2.5 text-[11.5px] transition " +
        (active
          ? "border-accent bg-accent text-white"
          : "border-border-2 bg-surface text-ink-2 hover:bg-surface-3")
      }
    >
      {children}
    </button>
  );
}

function MatchRing({ score }: { score: number }) {
  const tier = scoreTier(score);
  const deg = (score / 100) * 360;
  return (
    <div
      className="grid h-12 w-12 place-items-center rounded-full"
      style={{ background: `conic-gradient(${tier.ring} ${deg}deg, var(--surface-3) 0deg)` }}
    >
      <div className="grid h-9 w-9 place-items-center rounded-full bg-surface">
        <span className={`font-mono text-[13px] font-semibold ${tier.text}`}>{score}</span>
      </div>
    </div>
  );
}

function JobRow({ job, selected, onClick }: { job: Job; selected: boolean; onClick: () => void }) {
  const tier = job.score ? scoreTier(job.score.score_0_100) : null;
  const expired = job.board_state === "expired";
  return (
    <button
      onClick={onClick}
      data-testid="job-row"
      data-expired={expired}
      data-score-status={job.score_status}
      className={
        "flex w-full items-start gap-3 border-b border-border px-4 py-3 text-left transition-colors " +
        (expired ? "opacity-55 " : "") +
        (selected ? "bg-accent-wash/50" : "hover:bg-surface-2")
      }
    >
      <div
        className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-gradient-to-br from-accent-wash to-purple-wash text-[13px] font-semibold text-accent-ink"
        aria-hidden="true"
      >
        {initials(job.company)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13px] font-semibold text-ink">{job.title}</span>
        </div>
        {/* US-JB-01 row: company · location · work-style */}
        <div className="truncate text-[11.5px] text-ink-2">
          {[job.company, job.location, workLabel(job.work_style)].filter(Boolean).join(" · ")}
        </div>
        {/* salary · time-ago · N applicants */}
        <div className="text-[11.5px] text-ink-3">
          {[
            job.salary,
            timeAgo(job.posted_at),
            job.applicants !== null ? `${job.applicants} applicants` : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
        {/* skill chips + source pill (US-JB-01 + US-JB-10 informational variant) */}
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {job.tags.map((t) => (
            <span
              key={t}
              className="inline-flex h-[16px] items-center rounded-full bg-surface-3 px-1.5 text-[9.5px] text-ink-2"
            >
              {t}
            </span>
          ))}
          <span
            className={`inline-flex h-[16px] items-center rounded-full border px-1.5 font-mono text-[9.5px] uppercase tracking-wider ${sourceClasses(job.source_adapter)}`}
          >
            {job.source_adapter}
          </span>
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        {job.score ? (
          <span className={`font-mono text-[15px] font-semibold ${tier?.text}`}>
            {job.score.score_0_100}
          </span>
        ) : job.score_status === "failed" ? (
          <span
            data-testid="score-failed-badge"
            title="Scoring failed — usually the LLM provider was rate-limited or your key/session hit its usage cap. See Analytics → Scoring for the exact error; Remove and re-add to retry."
            className="inline-flex items-center rounded-full border border-bad/40 bg-bad-wash px-2 py-0.5 font-mono text-[10px] font-semibold text-bad"
          >
            Score failed
          </span>
        ) : (
          <span
            data-testid="match-score-badge"
            className="inline-flex items-center rounded-full border border-border-2 bg-surface-2 px-2 py-0.5 font-mono text-[10px] font-semibold text-ink-3"
          >
            Pending
          </span>
        )}
        {expired ? (
          <span
            data-testid="expired-label"
            className="inline-flex items-center rounded-full border border-border-2 bg-surface-2 px-1.5 py-0.5 text-[9.5px] font-medium uppercase tracking-wide text-ink-3"
          >
            Older listing
          </span>
        ) : null}
      </div>
    </button>
  );
}

function JobDetail({
  job,
  onSave,
  onRemove,
  onUnexpire,
  sourceFilter,
  onToggleSource,
  networkingEnabled,
  onFindReferrals,
  packetDefaults,
}: {
  job: Job | null;
  onSave: (j: Job, gen: { resume: boolean; cover: boolean }) => void;
  onRemove: (j: Job) => void;
  onUnexpire: (j: Job) => void;
  sourceFilter: string | null;
  onToggleSource: (adapter: string) => void;
  networkingEnabled: boolean;
  onFindReferrals: (j: Job) => void;
  packetDefaults: { resume: boolean; cl: boolean; refs: boolean };
}) {
  // Per-job automation toggles (US-JB-03), seeded from the Settings default
  // (auto-packet-on-save) and reset per job — prototype jobs.html semantics.
  const [toggles, setToggles] = useState({ ...packetDefaults });
  // Optimistic Save feedback (2026-07-11 #2): the POST is ~10 ms and every
  // on-Save op is queued async server-side — the button must not wait for the
  // refetch round-trip to acknowledge the click.
  const [justSaved, setJustSaved] = useState(false);
  // Row-level watchlist (approved-plan #4): add this job's whole company
  // board to the scan sources. null | "added" | "already" | "unsupported".
  const watchCompany = useWatchCompany();
  const [watchState, setWatchState] = useState<string | null>(null);
  const jobId = job?.id;
  useEffect(() => {
    setToggles({ ...packetDefaults });
    setJustSaved(false);
    setWatchState(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset per job only
  }, [jobId]);
  if (!job) {
    return (
      <div className="grid h-full place-items-center text-[12.5px] text-ink-3">
        Pick a job to see details.
      </div>
    );
  }
  const subtitle = [
    job.company,
    job.location,
    workLabel(job.work_style) || "Work style: N/A",
    job.salary || "Salary: N/A",
  ]
    .filter(Boolean)
    .join(" · ");
  const active = sourceFilter === job.source_adapter;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start gap-4 px-6 pt-6">
        <div
          className="grid h-14 w-14 shrink-0 place-items-center rounded-md bg-gradient-to-br from-accent-wash to-purple-wash text-[18px] font-semibold text-accent-ink"
          aria-hidden="true"
        >
          {initials(job.company)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[17px] font-semibold leading-tight text-ink">{job.title}</h2>
            <button
              data-testid="source-adapter-pill"
              data-source={job.source_adapter}
              onClick={() => onToggleSource(job.source_adapter)}
              className={
                "inline-flex h-[18px] items-center rounded-full border px-1.5 font-mono text-[10px] uppercase tracking-wider hover:opacity-80 " +
                sourceClasses(job.source_adapter) +
                (active ? " ring-2 ring-accent" : "")
              }
              title="Click to filter the list to this source"
            >
              {job.source_adapter}
            </button>
          </div>
          <div className="mt-1 text-[12.5px] text-ink-2">{subtitle}</div>
          <div className="text-[11.5px] text-ink-3">
            Posted: {job.posted_at || "N/A"}
          </div>
        </div>
        {job.score ? (
          <div className="flex flex-col items-center">
            <MatchRing score={job.score.score_0_100} />
            <span className="mt-1 text-[10.5px] uppercase tracking-wide text-ink-3">match</span>
          </div>
        ) : null}
      </div>

      {/* Sticky action row */}
      <div className="sticky top-0 z-10 mt-4 flex items-center gap-2 border-b border-border bg-surface px-6 py-3">
        <button
          onClick={() => {
            setJustSaved(true);
            onSave(job, { resume: toggles.resume, cover: toggles.cl });
            // Saving with "Find referrals" on kicks off discovery for the
            // company (US-NW-09 / US-REF-01).
            if (toggles.refs) onFindReferrals(job);
          }}
          data-testid="save-to-tracker"
          data-saved={job.saved || justSaved}
          className={
            "inline-flex h-[30px] min-w-[110px] items-center justify-center gap-1.5 rounded-7 border px-3 text-[12px] font-medium " +
            (job.saved || justSaved
              ? "border-good bg-good-wash text-good"
              : "border-accent bg-accent text-white hover:bg-accent-ink")
          }
        >
          <Icon name={job.saved || justSaved ? "bookmarkCheck" : "bookmark"} size={14} strokeWidth={2} />
          {job.saved || justSaved ? "Saved" : "Save"}
        </button>
        <button
          onClick={() => onRemove(job)}
          data-testid="remove-job"
          className="inline-flex h-[30px] min-w-[110px] items-center justify-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
        >
          <Icon name="trash" size={14} strokeWidth={2} />
          Remove
        </button>
        {/* Per-job automation toggles (US-JB-03). Referrals only when the
            networking master toggle is on (US-NW-09 / FR-SET-03); the
            "Application form answers" toggle stays retired (applier.md §2). */}
        <div className="ml-2 flex items-center gap-1.5" data-testid="jd-automation-toggles">
          {(["resume", "cl", ...(networkingEnabled ? (["refs"] as const) : [])] as const).map(
            (slot) => {
              const on = toggles[slot];
              const label =
                slot === "resume" ? "Resume"
                : slot === "cl" ? "Cover letter"
                : "Find referrals";
              return (
                <button
                  key={slot}
                  data-on={on}
                  data-testid={slot === "refs" ? "jd-referrals-toggle" : undefined}
                  onClick={() => setToggles((t) => ({ ...t, [slot]: !t[slot] }))}
                  className={
                    "inline-flex h-[30px] items-center gap-1.5 rounded-7 border px-2 text-[11.5px] font-medium " +
                    (on
                      ? "border-accent bg-accent-wash text-accent-ink"
                      : "border-border-2 bg-surface text-ink-3 hover:bg-surface-3")
                  }
                >
                  <span
                    className={
                      "relative inline-block h-3.5 w-6 rounded-full transition-colors " +
                      (on ? "bg-accent" : "bg-border-2")
                    }
                  >
                    <span
                      className={
                        "absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white transition-all " +
                        (on ? "left-[13px]" : "left-0.5")
                      }
                    />
                  </span>
                  {label}
                </button>
              );
            },
          )}
        </div>
        {job.board_state === "expired" ? (
          <button
            onClick={() => onUnexpire(job)}
            data-testid="unexpire-job"
            title="Restore this older listing to the active feed"
            className="inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
          >
            <Icon name="bookmark" size={14} strokeWidth={2} />
            Restore listing
          </button>
        ) : null}
        <div className="flex-1" />
        <button
          data-testid="watch-company"
          title="Scan this company's whole board on every future scan"
          disabled={watchCompany.isPending || watchState === "added"}
          onClick={() =>
            watchCompany.mutate(
              { job_id: job.id },
              {
                onSuccess: (r) => setWatchState(r.added ? "added" : "already"),
                onError: () => setWatchState("unsupported"),
              },
            )
          }
          className="inline-flex h-[30px] items-center gap-1 rounded-7 px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 disabled:opacity-70"
        >
          {watchState === "added"
            ? "Watching company ✓"
            : watchState === "already"
              ? "Already watched"
              : watchState === "unsupported"
                ? "Can't watch this source"
                : "Watch company"}
        </button>
        <a
          href={job.canonical_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-[30px] items-center gap-1 rounded-7 px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
        >
          Open posting ↗
        </a>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {/* JD | Scoring as equal columns (maintainer, 2026-07-11) — the scoring
            table needs real width, matching the detail modal's Scoring tab. */}
        <div className="grid grid-cols-2 gap-6 px-6 py-5">
          <Markdown md={job.description} />
          <aside className="flex flex-col gap-4">
            <div className="rounded-lg border border-border bg-surface-2 p-4">
              <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-ink-3">
                Match score
              </h3>
              {job.score ? (
                <>
                  <ul data-testid="match-reasons" className="space-y-1.5 text-[12.5px] text-ink-2">
                    {job.score.reasons.slice(0, 4).map((r, i) => (
                      <li key={i} className="flex gap-1.5">
                        <span className="mt-1 size-1 shrink-0 rounded-full bg-accent" aria-hidden="true" />
                        <span>{r}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-3 border-t border-border pt-3">
                    <Markdown md={job.score.breakdown_md} className="text-[12px]" />
                  </div>
                </>
              ) : (
                <p className="text-[12px] text-ink-3">Scoring this job — refresh in a moment.</p>
              )}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

// Explained empty-feed state (US-JB-09 / FR-JB-10): scrape-running (spinner +
// live "N found so far", no time estimate), scrape-empty, or the last-scrape
// error with a timestamp — never a silent blank board.
function BoardEmptyState({
  meta,
  loading,
  filteredOut,
}: {
  meta: BoardPage | undefined;
  loading: boolean;
  filteredOut: boolean;
}) {
  const wrap = "grid h-full place-items-center px-6 text-center text-[12.5px] text-ink-3";
  // Filters or a search hid every row — distinct from a genuinely empty scrape.
  if (filteredOut) {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="filtered">
        No jobs match these filters or search.
      </div>
    );
  }
  const status = loading ? "running" : (meta?.scan_status ?? "empty");
  if (status === "running") {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-running">
        <div className="flex flex-col items-center gap-2">
          <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
          <div className="font-medium text-ink-2">Scanning job boards…</div>
          <div data-testid="scrape-found-count">
            {meta?.total ?? 0} jobs found so far
          </div>
        </div>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-error">
        <div className="flex flex-col items-center gap-1">
          <div className="font-medium text-bad">The last scrape failed.</div>
          <div className="max-w-[360px] text-ink-3">{meta?.scan_error || "Unknown error."}</div>
          <div className="text-ink-4">Last attempt {shortAgo(meta?.last_scan_at)}.</div>
        </div>
      </div>
    );
  }
  // scrape-empty (genuinely zero rows) — offer the escape hatches.
  return (
    <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-empty">
      <div className="flex flex-col items-center gap-1">
        <div className="font-medium text-ink-2">No jobs matched your roles and locations.</div>
        <div className="text-ink-3">
          Widen your Job finder preferences or add a job by URL.
        </div>
        <div className="text-ink-4">Last refresh {shortAgo(meta?.last_scan_at)}.</div>
      </div>
    </div>
  );
}

export function JobBoard() {
  // Board search (FR-JB-13): listSearch = shallow title/company/location match
  // above the list; textSearch = deep all-text match (JD + score texts) next to
  // Sort. Both run server-side (the feed is paginated — filtering loaded pages
  // client-side would miss matches on unloaded pages), debounced per keystroke.
  const [listSearch, setListSearch] = useState("");
  const [textSearch, setTextSearch] = useState("");
  const listQ = useDebounced(listSearch.trim(), 250);
  const textQ = useDebounced(textSearch.trim(), 250);
  const board = useBoard(listQ, textQ);
  const { data: trashed = [] } = useTrash();
  const saveJob = useSaveJob();
  const trashJob = useTrashJob();
  const emptyTrash = useEmptyTrash();
  const tombstoneJob = useTombstoneJob();
  const addByUrl = useAddJobByUrl();
  const { data: settings } = useSettings();
  const { data: profile } = useProfile();
  const updateProfile = useUpdateProfile();
  const session = useLinkedInSession();
  const discoverReferrals = useDiscoverReferrals();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFilter>("ALL");
  const [ws, setWs] = useState<WorkStyleFilter>("ALL");
  const [sort, setSort] = useState<SortMode>("match");
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);
  const [salaryMin, setSalaryMin] = useState(0);
  const [posted, setPosted] = useState<number>(0); // 0=all
  const [split, setSplit] = useState<number>(() => {
    const s = Number(localStorage.getItem(SPLIT_KEY));
    return s >= 20 && s <= 80 ? s : 32;
  });
  const [showAdd, setShowAdd] = useState(false);
  const [showTrash, setShowTrash] = useState(false);
  const [showMaster, setShowMaster] = useState(false);
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

  const visible = useMemo(() => {
    // Server already excludes Saved and Trashed; Expired rows stay (greyed).
    let list = [...boardJobs];
    if (sourceFilter) list = list.filter((j) => j.source_adapter === sourceFilter);
    if (ws === "REMOTE_FRIENDLY") {
      list = list.filter(
        (j) => j.work_style === "REMOTE" || /remote/i.test(j.location),
      );
    } else if (ws !== "ALL") {
      list = list.filter((j) => j.work_style === ws);
    }
    if (status === "SCORED") list = list.filter((j) => j.score_status === "scored");
    if (status === "PENDING") list = list.filter((j) => j.score_status === "pending");
    if (status === "FAILED") list = list.filter((j) => j.score_status === "failed");
    if (posted > 0) {
      list = list.filter((j) => {
        if (!j.posted_at) return false;
        const days = (Date.now() - new Date(j.posted_at).getTime()) / 86_400_000;
        return days <= posted;
      });
    }
    if (salaryMin > 0) {
      // FR-JB-04: best-effort band match; an unparseable/absent salary is never
      // silently hidden (rank-don't-gate) — only a parseable, below-band salary is.
      list = list.filter((j) => {
        const floor = salaryFloor(j.salary);
        return floor === null || floor >= salaryMin;
      });
    }
    list = [...list].sort((a, b) => {
      if (sort === "recency") return (b.posted_at || "").localeCompare(a.posted_at || "");
      const sa = a.score?.score_0_100 ?? -1;
      const sb = b.score?.score_0_100 ?? -1;
      if (sb !== sa) return sb - sa;
      return (b.posted_at || "").localeCompare(a.posted_at || "");
    });
    return list;
  }, [boardJobs, sourceFilter, ws, status, posted, salaryMin, sort]);

  const selected = visible.find((j) => j.id === selectedId) ?? visible[0] ?? null;

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
        <h1 className="text-[14px] font-semibold text-ink">Job Board</h1>
        <div className="ml-auto flex items-center gap-3 py-1.5">
          <button
            onClick={() => setShowPrefs(true)}
            data-testid="finder-prefs"
            title="Configure background scraping and scoring"
            className="inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink"
          >
            <Icon name="settings" size={14} strokeWidth={2} />
            Job finder preferences
          </button>
          <button
            onClick={() => setShowMaster(true)}
            data-action="open-master-resume"
            title="Jobs in the board are scored and ranked based on your Master Resume. Click to view and edit."
            className="inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink"
          >
            <Icon name="file" size={14} strokeWidth={2} />
            Master Resume
          </button>
          <button
            onClick={() => setShowTrash(true)}
            data-testid="trash-btn"
            className="relative inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink"
          >
            <Icon name="trash" size={14} strokeWidth={2} />
            Deleted Jobs
            {trashed.length > 0 ? (
              <span className="ml-1 inline-flex h-[16px] min-w-[16px] items-center justify-center rounded-full bg-bad px-1 font-mono text-[10px] font-bold text-white">
                {trashed.length}
              </span>
            ) : null}
          </button>
          <button
            onClick={() => setShowAdd(true)}
            data-testid="add-job"
            className="inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
          >
            <Icon name="plus" size={14} strokeWidth={2} />
            Add a job or company
          </button>
        </div>
      </header>

      {/* Filter row */}
      <div
        data-testid="v2-filters"
        className="flex flex-wrap items-center gap-2 border-b border-border bg-surface px-5 py-2"
      >
        {/* Work style chip group (US-JB-02) — first, per the prototype filter row */}
        <div className="flex items-center gap-1.5" id="filter-ws" aria-label="Work style">
          <span className="text-[11.5px] uppercase tracking-wider text-ink-4">Work style</span>
          {(
            [
              ["ALL", "All work styles"],
              ["REMOTE", "Remote"],
              ["HYBRID", "Hybrid"],
              ["ONSITE", "Onsite"],
              ["REMOTE_FRIENDLY", "Remote-friendly"],
            ] as [WorkStyleFilter, string][]
          ).map(([v, l]) => (
            <Chip key={v} active={ws === v} onClick={() => setWs(v)}>
              {l}
            </Chip>
          ))}
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] uppercase tracking-wider text-ink-4">Posted</span>
          {[
            [0, "Any time"],
            [1, "24h"],
            [7, "7d"],
            [30, "30d"],
          ].map(([v, l]) => (
            <Chip key={v} active={posted === v} onClick={() => setPosted(v as number)}>
              {l}
            </Chip>
          ))}
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] uppercase tracking-wider text-ink-4">Min salary</span>
          {[
            [0, "Any salary"],
            [50000, "≥ $50k"],
            [100000, "≥ $100k"],
            [150000, "≥ $150k"],
          ].map(([v, l]) => (
            <Chip key={v} active={salaryMin === v} onClick={() => setSalaryMin(v as number)}>
              {l}
            </Chip>
          ))}
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5" id="filter-status">
          <span className="text-[11.5px] uppercase tracking-wider text-ink-4">Status</span>
          {(["ALL", "SCORED", "PENDING", "FAILED"] as StatusFilter[]).map((s) => (
            <Chip key={s} active={status === s} onClick={() => setStatus(s)}>
              {s[0] + s.slice(1).toLowerCase()}
            </Chip>
          ))}
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] uppercase tracking-wider text-ink-4">Sort</span>
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
                {m === "match" ? "Match score" : "Recency"}
              </button>
            ))}
          </div>
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        {/* Deep all-text search (FR-JB-13): titles, JD bodies + match-score texts. */}
        <SearchBox
          value={textSearch}
          onChange={setTextSearch}
          placeholder="Search everything — JDs, scores…"
          testid="board-text-search"
          className="w-[260px]"
        />
        <span className="ml-auto" />
      </div>

      {/* Split */}
      <div className="flex min-h-0 flex-1" data-testid="v2-split-host">
        <div className="flex min-w-0 flex-col border-r border-border" style={{ flexBasis: `${split}%` }}>
          <div className="flex items-center justify-between border-b border-border px-4 py-2 text-[12px] text-ink-3">
            <span data-testid="job-list-count">
              {/* Live total from the server, not the loaded/filtered count (FR-JB-02);
                  real "last refresh" from the last successful scan (FR-JB-10). */}
              {meta?.total ?? visible.length} jobs{" "}
              <span className="text-ink-3/80" data-testid="last-refresh">
                · last refresh {shortAgo(meta?.last_scan_at)}
              </span>
            </span>
          </div>
          {/* List search (FR-JB-13): shallow title/company/location match over the
              whole feed (server-side); clearing restores the full list. */}
          <div className="border-b border-border px-3 py-2">
            <SearchBox
              value={listSearch}
              onChange={setListSearch}
              placeholder="Search this list — title, company, location…"
              testid="board-list-search"
            />
          </div>
          <div
            role="listbox"
            aria-label="Jobs"
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
                filteredOut={boardJobs.length > 0 || Boolean(listQ || textQ)}
              />
            ) : (
              <>
                {visible.map((j) => (
                  <JobRow
                    key={j.id}
                    job={j}
                    selected={selected?.id === j.id}
                    onClick={() => setSelectedId(j.id)}
                  />
                ))}
                {board.hasNextPage ? (
                  <div className="grid place-items-center py-3">
                    <button
                      data-testid="board-load-more"
                      onClick={() => void board.fetchNextPage()}
                      disabled={board.isFetchingNextPage}
                      className="rounded-full border border-border-2 bg-surface px-3 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3 disabled:opacity-50"
                    >
                      {board.isFetchingNextPage ? "Loading…" : "Load more jobs"}
                    </button>
                  </div>
                ) : null}
              </>
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
        <AddByUrlModal onClose={() => setShowAdd(false)} onAdd={(draft) => addByUrl.mutateAsync(draft)} />
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
      {showMaster && profile ? (
        <ResumeModal
          kind="master"
          profile={profile}
          onClose={() => setShowMaster(false)}
          onSaveMaster={(md: string) => updateProfile.mutate(md)}
        />
      ) : null}
      {showPrefs ? <FinderPrefsModal onClose={() => setShowPrefs(false)} /> : null}
    </>
  );
}

// ─── Job finder preferences (US-JB-01 topbar / US-SET-01; doubles as settings-prefs) ──

function PrefChipInput({
  label,
  hint,
  items,
  onRemove,
  onAdd,
  placeholder,
  testid,
}: {
  label: string;
  hint: string;
  items: string[];
  onRemove: (v: string) => void;
  onAdd: (v: string) => void;
  placeholder: string;
  testid: string;
}) {
  const [input, setInput] = useState("");
  return (
    <section className="space-y-2">
      <header>
        <h3 className="text-[13px] font-semibold text-ink">{label}</h3>
        <p className="text-[11.5px] text-ink-3">{hint}</p>
      </header>
      <div
        data-testid={testid}
        className="flex min-h-[36px] flex-wrap items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-2 py-1.5 focus-within:border-accent"
      >
        {items.map((it) => (
          <span
            key={it}
            className="inline-flex items-center gap-1 rounded-full border border-border-2 bg-surface-2 px-2 py-0.5 text-[12px] text-ink"
          >
            {it}
            <button
              type="button"
              onClick={() => onRemove(it)}
              className="text-ink-3 hover:text-bad"
              aria-label="Remove"
            >
              ×
            </button>
          </span>
        ))}
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === ",") && input.trim()) {
              e.preventDefault();
              onAdd(input.trim());
              setInput("");
            }
          }}
          placeholder={placeholder}
          className="min-w-[120px] flex-1 bg-transparent text-[12.5px] text-ink placeholder:text-ink-4 focus:outline-none"
        />
      </div>
    </section>
  );
}

function RadioRow({
  options,
  value,
  onChange,
  testid,
}: {
  options: string[];
  value: string;
  onChange: (v: string) => void;
  testid: string;
}) {
  return (
    <div
      data-testid={testid}
      role="radiogroup"
      className="inline-flex rounded-7 border border-border-2 bg-surface p-0.5"
    >
      {options.map((o) => (
        <button
          key={o}
          type="button"
          role="radio"
          aria-checked={value === o}
          onClick={() => onChange(o)}
          className={
            "h-[28px] rounded-[5px] px-3 text-[12.5px] font-medium " +
            (value === o ? "bg-accent text-white" : "text-ink-2 hover:bg-surface-3")
          }
        >
          {o}
        </button>
      ))}
    </div>
  );
}

// Tracked companies (job-finder-preferences design 2026-07-21): the roster
// view of the `watched` [[sources]] rows the per-job "Watch company" action
// writes — same data, now listable/removable/addable from one place.
function TrackedCompanies() {
  const { data: entries } = useWatchlist();
  const watchCompany = useWatchCompany();
  const unwatch = useUnwatchCompany();
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  async function add() {
    if (!url.trim()) return;
    setError("");
    try {
      await watchCompany.mutateAsync({ url: url.trim() });
      setUrl("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section className="space-y-2" data-testid="fp-tracked-companies">
      <header>
        <h3 className="text-[13px] font-semibold text-ink">Tracked companies</h3>
        <p className="text-[11.5px] text-ink-3">
          Boards every scan covers. Add one by pasting a careers page on a supported ATS, or use
          “Watch company” on any job.
        </p>
      </header>
      <ul className="space-y-1">
        {(entries ?? []).map((e) => (
          <li
            key={e.url}
            className="flex items-center justify-between gap-2 rounded-7 border border-border-2 bg-surface px-2.5 py-1.5"
            data-testid="fp-tracked-row"
          >
            <div className="min-w-0">
              <div className="truncate text-[12.5px] font-medium text-ink">
                {e.company || e.url}
              </div>
              <div className="truncate font-mono text-[11px] text-ink-3">
                {e.adapter ? `${e.adapter} · ` : ""}
                {e.url}
              </div>
            </div>
            <button
              type="button"
              onClick={() => unwatch.mutate(e.url)}
              className="text-ink-3 hover:text-bad"
              aria-label="Stop tracking"
              data-testid="fp-tracked-remove"
            >
              ×
            </button>
          </li>
        ))}
        {(entries ?? []).length === 0 ? (
          <li className="rounded-7 border border-dashed border-border-2 px-2.5 py-1.5 text-[12px] text-ink-3">
            Nothing tracked yet.
          </li>
        ) : null}
      </ul>
      <div className="flex items-center gap-2">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void add();
            }
          }}
          placeholder="https://boards.greenhouse.io/…"
          data-testid="fp-tracked-url"
          className="h-[30px] flex-1 rounded-7 border border-border-2 bg-surface px-2 text-[12.5px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void add()}
          disabled={watchCompany.isPending || !url.trim()}
          data-testid="fp-tracked-add"
          className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
        >
          {watchCompany.isPending ? "Adding…" : "Track"}
        </button>
      </div>
      {error ? (
        <p className="text-[11.5px] text-bad" data-testid="fp-tracked-error">
          {error}
        </p>
      ) : null}
    </section>
  );
}

// Freshness label ⇄ days (0 = "Any" = no freshness window, ScanPrefs semantics).
const FINDER_FRESHNESS_DAYS: Record<string, number> = { "24h": 1, "7d": 7, "30d": 30, Any: 0 };
const FINDER_FRESHNESS_LABEL: Record<number, string> = { 1: "24h", 7: "7d", 30: "30d", 0: "Any" };

function FinderPrefsModal({
  onClose,
}: {
  onClose: () => void;
}) {
  // Seeded from the STORED preferences and saved back through the same
  // /api/settings path onboarding uses — which also threads the cadence into
  // the scan schedule server-side. (2026-07-12 audit P0-2: this modal was a
  // hardcoded mock stub whose "Save & rescan" rescanned with stale prefs.)
  const { data: settings } = useSettings();
  const qc = useQueryClient();
  const [roles, setRoles] = useState<string[]>(settings?.job_prefs.role_aliases ?? []);
  const [locations, setLocations] = useState<string[]>(settings?.job_prefs.locations ?? []);
  const [freshness, setFreshness] = useState(
    FINDER_FRESHNESS_LABEL[settings?.job_prefs.freshness_days ?? 7] ?? "7d",
  );
  const [cadence, setCadence] = useState(settings?.job_prefs.scrape_cadence ?? "Every 24h");
  const [excludedCompanies, setExcludedCompanies] = useState<string[]>(
    settings?.job_prefs.excluded_companies ?? [],
  );
  const [excludedKeywords, setExcludedKeywords] = useState<string[]>(
    settings?.job_prefs.excluded_keywords ?? [],
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const { data: profile } = useProfile();
  const triggerScan = useTriggerScan();
  const masterName = firstHeading(profile?.master_md);

  async function saveAndRescan() {
    setSaving(true);
    setSaveError("");
    try {
      // networking_enabled deliberately omitted — this modal never touches it.
      await api.savePreferences({
        role_aliases: roles,
        locations,
        freshness_days: FINDER_FRESHNESS_DAYS[freshness] ?? 7,
        scrape_cadence: cadence,
        excluded_companies: excludedCompanies,
        excluded_keywords: excludedKeywords,
      });
      await qc.invalidateQueries({ queryKey: qk.settings });
      triggerScan.mutate(); // rescan now runs against the values just saved
      onClose();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  return (
    <Modal title="Job finder preferences" onClose={onClose} width={560}>
      <form
        className="flex flex-col gap-5 px-5 py-5"
        onSubmit={(e) => {
          e.preventDefault();
          void saveAndRescan();
        }}
      >
        <p className="-mt-2 text-[12px] text-ink-3">
          Controls the background scraper that fills your Job Board. Every match is scored against
          your master resume when it lands.
        </p>
        <PrefChipInput
          label="Roles to search"
          hint="Titles the scraper queries for. Press Enter or comma to add."
          items={roles}
          onAdd={(v) => setRoles((r) => (r.includes(v) ? r : [...r, v]))}
          onRemove={(v) => setRoles((r) => r.filter((x) => x !== v))}
          placeholder="Add a role…"
          testid="fp-roles"
        />
        <PrefChipInput
          label="Locations"
          hint="Cities or regions to search in. Remote is valid. (Work style is a board filter — the chips above the feed.)"
          items={locations}
          onAdd={(v) => setLocations((r) => (r.includes(v) ? r : [...r, v]))}
          onRemove={(v) => setLocations((r) => r.filter((x) => x !== v))}
          placeholder="Add a location…"
          testid="fp-locations"
        />
        <PrefChipInput
          label="Excluded companies"
          hint="Never show jobs from these companies (current employer, blocklist). Word-boundary match."
          items={excludedCompanies}
          onAdd={(v) => setExcludedCompanies((r) => (r.includes(v) ? r : [...r, v]))}
          onRemove={(v) => setExcludedCompanies((r) => r.filter((x) => x !== v))}
          placeholder="Add a company…"
          testid="fp-exclude-companies"
        />
        <PrefChipInput
          label="Excluded keywords"
          hint="Skip postings whose description contains any of these (e.g. “unpaid”, “clearance required”)."
          items={excludedKeywords}
          onAdd={(v) => setExcludedKeywords((r) => (r.includes(v) ? r : [...r, v]))}
          onRemove={(v) => setExcludedKeywords((r) => r.filter((x) => x !== v))}
          placeholder="Add a keyword…"
          testid="fp-exclude-keywords"
        />
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">Posting freshness</h3>
            <p className="text-[11.5px] text-ink-3">
              Skip postings older than this on every scrape. (Cold-start ignores this and pulls the
              past 30 days.)
            </p>
          </header>
          <RadioRow
            options={["24h", "7d", "30d", "Any"]}
            value={freshness}
            onChange={setFreshness}
            testid="fp-freshness"
          />
        </section>
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">Background scrape cadence</h3>
            <p className="text-[11.5px] text-ink-3">
              We run scraping once per selected time frame, then score every match against your
              master resume. Tighter cadence = fresher feed, more bandwidth.
            </p>
          </header>
          <RadioRow
            options={["Every 6h", "Every 12h", "Every 24h", "Every 48h", "Every 72h"]}
            value={cadence}
            onChange={setCadence}
            testid="fp-cadence"
          />
        </section>
        <TrackedCompanies />
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">Master resume</h3>
            <p className="text-[11.5px] text-ink-3">Used to score every job in your feed.</p>
          </header>
          <div className="flex items-center justify-between gap-3 rounded-7 border border-border-2 bg-surface-2 px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-[12.5px] font-medium text-ink">
                {masterName ? `${masterName} — master` : "No master resume yet"}
              </div>
              <div className="font-mono text-[11px] text-ink-3">
                {masterName ? "Ready to score your feed" : "Add one to score & tailor"}
              </div>
            </div>
            {/* View/Replace opens the master-resume popup — returns with the
                resume-popup commit. */}
          </div>
        </section>
        <div className="-mx-5 -mb-5 mt-2 flex items-center justify-end gap-2 border-t border-border bg-surface-2 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2 hover:text-ink"
          >
            Cancel
          </button>
          {saveError ? (
            <span className="text-[11.5px] text-bad" data-testid="finder-prefs-error">
              {saveError}
            </span>
          ) : null}
          <button
            type="submit"
            disabled={saving || roles.length === 0 || locations.length === 0}
            data-testid="finder-prefs-save"
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save & rescan"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ─── Add-by-URL (US-JB-07) ────────────────────────────────────────────────────

function AddByUrlModal({
  onClose,
  onAdd,
}: {
  onClose: () => void;
  onAdd: (draft: JobDraft) => Promise<unknown>;
}) {
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<"entry" | "fetching" | "editing">("entry");
  const [draft, setDraft] = useState<JobDraft | null>(null);
  // Set when the pasted URL was permanently deleted (tombstoned) — trash is
  // recoverable, a tombstone is final, so re-add is impossible (US-JB-07).
  const [tombstoned, setTombstoned] = useState(false);
  const preview = useJobPreview();
  // Watchlist path (approved-plan #4): the same paste box also accepts a
  // company careers URL — "watch" adds it as a permanent scan source.
  const watchCompany = useWatchCompany();
  const [watchMsg, setWatchMsg] = useState<string | null>(null);

  function fetchDetails() {
    setPhase("fetching");
    setTombstoned(false);
    preview.mutate(url, {
      onSuccess: (d) => {
        setDraft(d);
        setPhase("editing");
      },
      onError: (err) => {
        if (err instanceof JobTombstonedError) {
          // Final — don't offer an editable form; show the honest copy.
          setTombstoned(true);
          setPhase("entry");
          return;
        }
        // Other fetch failures: still let the user fill fields by hand
        // (rank-don't-gate escape hatch, US-JB-07).
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
    void onAdd(draft)
      .then(() => onClose())
      .catch((err: unknown) => {
        if (err instanceof JobTombstonedError) {
          setTombstoned(true);
          setPhase("entry");
        }
        // Any other error surfaces via the mutation; leave the form open.
      });
  }

  function patch(fields: Partial<JobDraft>) {
    setDraft((d) => (d ? { ...d, ...fields } : d));
  }

  return (
    <Modal title="Add a job or company" onClose={onClose} width={520}>
      {phase === "entry" ? (
        <form
          className="flex flex-col gap-3 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            fetchDetails();
          }}
        >
          <label className="text-[12.5px] text-ink-2">
            Paste a job posting URL to add that job — or a company&apos;s careers page to
            watch their whole board on every scan.
          </label>
          {tombstoned ? (
            <p
              data-testid="add-job-tombstoned"
              className="rounded-md border border-bad/40 bg-bad-wash px-3 py-2 text-[12px] text-bad"
            >
              This job was permanently deleted and can't be re-added. If you still want to track
              it, keep a record of it outside the app.
            </p>
          ) : null}
          <input
            type="url"
            required
            autoFocus
            data-testid="add-job-url-input"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setTombstoned(false);
            }}
            placeholder="https://company.com/careers/senior-engineer"
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
          />
          {watchMsg ? (
            <p
              data-testid="watch-company-result"
              className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12px] text-ink-2"
            >
              {watchMsg}
            </p>
          ) : null}
          <div className="mt-1 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="watch-company-btn"
              disabled={!url || watchCompany.isPending}
              onClick={() =>
                watchCompany.mutate(
                  { url },
                  {
                    onSuccess: (r) =>
                      setWatchMsg(
                        r.added
                          ? `Watching ${r.company || r.source_url} — every scan now covers this board (${r.adapter}).`
                          : "Already watching this board.",
                      ),
                    onError: (err: unknown) =>
                      setWatchMsg(err instanceof Error ? err.message : "Could not watch this URL."),
                  },
                )
              }
              className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] font-medium text-ink-2 hover:bg-surface-3 disabled:opacity-50"
            >
              Watch company board
            </button>
            <button
              type="submit"
              data-testid="add-job-fetch-btn"
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
            >
              Fetch job details
            </button>
          </div>
        </form>
      ) : phase === "fetching" ? (
        <div className="grid place-items-center px-5 py-10 text-[13px] text-ink-3">
          <div className="flex items-center gap-2">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
            Fetching job details…
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
            {url || "(no URL)"}{" "}
            <button
              type="button"
              onClick={() => setPhase("entry")}
              className="text-accent hover:underline"
            >
              · Re-fetch
            </button>
          </div>
          <input
            value={draft?.title ?? ""}
            onChange={(e) => patch({ title: e.target.value })}
            placeholder="Title"
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <input
            value={draft?.company ?? ""}
            onChange={(e) => patch({ company: e.target.value })}
            placeholder="Company"
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <input
            value={draft?.location ?? ""}
            onChange={(e) => patch({ location: e.target.value })}
            placeholder="Location"
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <textarea
            value={draft?.description ?? ""}
            onChange={(e) => patch({ description: e.target.value })}
            placeholder="Description"
            rows={5}
            data-testid="add-job-description"
            className="resize-y rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <div className="mt-1 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              Cancel
            </button>
            <button
              type="submit"
              data-testid="add-job-submit-btn"
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
            >
              Add to Job Board
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}

// ─── Trash (US-JB-11) ─────────────────────────────────────────────────────────

function TrashModal({
  trashed,
  onClose,
  onUndo,
  onDeleteForever,
  onEmpty,
}: {
  trashed: Job[];
  onClose: () => void;
  onUndo: (id: string) => void;
  onDeleteForever: (id: string) => void;
  onEmpty: () => void;
}) {
  // Two-step confirms before anything irreversible (US-JB-11 ethos: the user
  // signs off on every irreversible action). `confirmId` = per-row Delete
  // forever; `confirmEmpty` = Empty Trash.
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  return (
    <Modal
      title="Deleted Jobs"
      onClose={onClose}
      width={520}
      footer={
        <div className="flex items-center justify-between gap-3 text-[11.5px] text-ink-3">
          <span>Jobs in Trash are permanently removed after 7 days.</span>
          {confirmEmpty ? (
            <span className="flex items-center gap-2">
              <span className="text-ink-2">
                Empty Trash? {trashed.length} {trashed.length === 1 ? "job" : "jobs"} will be
                permanently removed.
              </span>
              <button
                data-testid="trash-empty-confirm-btn"
                onClick={() => {
                  onEmpty();
                  setConfirmEmpty(false);
                }}
                className="rounded-md border border-bad/40 bg-bad px-2 py-1 font-medium text-white hover:opacity-90"
              >
                Confirm
              </button>
              <button
                onClick={() => setConfirmEmpty(false)}
                className="rounded-md border border-border px-2 py-1 text-ink-2 hover:bg-surface-3"
              >
                Cancel
              </button>
            </span>
          ) : (
            <button
              data-testid="trash-empty-btn"
              disabled={trashed.length === 0}
              onClick={() => setConfirmEmpty(true)}
              className="rounded-md border border-bad/40 px-2 py-1 text-bad hover:bg-bad-wash disabled:opacity-40 disabled:hover:bg-transparent"
            >
              Empty Trash
            </button>
          )}
        </div>
      }
    >
      <div data-testid="trash-modal" className="px-5 py-4">
        {trashed.length === 0 ? (
          <p className="text-[13px] text-ink-3">No removed jobs.</p>
        ) : (
          <ul className="space-y-2">
            {trashed.map((j) => (
              <li key={j.id} className="flex items-center gap-3 rounded-md border border-border px-3 py-2">
                <div className="grid h-8 w-8 place-items-center rounded bg-surface-2 text-[11px] font-semibold text-ink-2">
                  {initials(j.company)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium text-ink">{j.title}</div>
                  <div className="text-[11px] text-ink-3">{j.company} · Removed recently</div>
                </div>
                {confirmId === j.id ? (
                  <>
                    <button
                      data-testid="trash-delete-forever-confirm-btn"
                      onClick={() => {
                        onDeleteForever(j.id);
                        setConfirmId(null);
                      }}
                      className="rounded-md border border-bad/40 bg-bad px-2 py-1 text-[11.5px] font-medium text-white hover:opacity-90"
                    >
                      Delete forever
                    </button>
                    <button
                      onClick={() => setConfirmId(null)}
                      className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      data-testid="trash-undo-btn"
                      onClick={() => onUndo(j.id)}
                      className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                    >
                      Undo
                    </button>
                    <button
                      data-testid="trash-delete-forever-btn"
                      title="Delete forever"
                      onClick={() => setConfirmId(j.id)}
                      className="rounded-md border border-bad/40 px-2 py-1 text-[11.5px] text-bad hover:bg-bad-wash"
                    >
                      Delete forever
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
