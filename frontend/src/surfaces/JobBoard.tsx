// Job Board (US-JB-01..07, 09, 10, 11, 12) — two-column scored feed + JD detail,
// filters/sort, board search (list + deep all-text), resizable persisted split,
// Save/Remove, Add-by-URL, Trash, source filter, master-resume popup.
// Ports design/prototype/prototype-modal/jobs.html.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api";
import i18n from "../i18n";
import { invalidateFeed, qk } from "../api/queries";

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
  useSchedules,
  useTriggerScan,
  useUnwatchCompany,
  useWatchCompany,
  useWatchlist,
} from "../api/queries";
import { ApiError } from "../api/real";
import {
  JobTombstonedError,
  type BoardPage,
  type Job,
  type JobDraft,
  type RescorePreview,
} from "../api/types";
import { HeaderAddButton, HeaderDeletedButton } from "../shell/HeaderAddButton";
import { Icon } from "../shell/icons";
import { Chip, SearchBox } from "../shell/FilterRow";
import { Modal } from "../shell/Modal";
import { RescoreAiDialog } from "../shell/RescoreAiDialog";
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

// Every job always carries at least a keyword score (the instant on-device
// floor), so "Pending"/"Failed" are gone — the filter is now which scorer
// produced the displayed score (maintainer 2026-07-22).
type StatusFilter = "ALL" | "AI" | "KEYWORD";
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
function MatchRing({ score, keyword = false }: { score: number; keyword?: boolean }) {
  // Keyword scores render grey with a dashed ring — never the tier-colored
  // conic gradient — so they can't be mistaken for an AI score (Scoring
  // modes, 2026-07-22).
  if (keyword) {
    return (
      <div
        className="grid h-12 w-12 place-items-center rounded-full border-2 border-dashed border-border-2 bg-surface-2"
        data-testid="keyword-score-ring"
      >
        <span className="font-mono text-[13px] font-semibold text-ink-3">{score}</span>
      </div>
    );
  }
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

// Search-match highlighting (maintainer 2026-07-22): wherever the board search
// found text, show it — list rows and the open JD alike.
function highlight(text: string, q: string): ReactNode {
  if (!q || !text) return text;
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  if (!lower.includes(needle)) return text;
  const parts: ReactNode[] = [];
  let i = 0;
  for (;;) {
    const at = lower.indexOf(needle, i);
    if (at === -1) {
      parts.push(text.slice(i));
      break;
    }
    if (at > i) parts.push(text.slice(i, at));
    parts.push(
      <mark key={at} className="rounded-[2px] bg-warn-wash text-inherit">
        {text.slice(at, at + q.length)}
      </mark>,
    );
    i = at + q.length;
  }
  return <>{parts}</>;
}

function JobRow({
  job,
  selected,
  onClick,
  q = "",
}: {
  job: Job;
  selected: boolean;
  onClick: () => void;
  q?: string;
}) {
  const { t } = useTranslation();
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
          <span className="truncate text-[13px] font-semibold text-ink">
            {highlight(job.title, q)}
          </span>
          {/* Inserted by the latest succeeded scan (isNew on the DTO). */}
          {job.is_new ? (
            <span
              data-testid="new-badge"
              className="inline-flex h-[16px] shrink-0 items-center rounded-full bg-accent-wash px-1.5 text-[9px] font-semibold text-accent-ink"
            >
              {t("jobBoard.row.new")}
            </span>
          ) : null}
        </div>
        {/* US-JB-01 row: company · location · work-style */}
        <div className="truncate text-[11.5px] text-ink-2">
          {highlight(
            [job.company, job.location, workLabel(job.work_style)].filter(Boolean).join(" · "),
            q,
          )}
        </div>
        {/* salary · time-ago · N applicants */}
        <div className="text-[11.5px] text-ink-3">
          {[
            job.salary,
            timeAgo(job.posted_at),
            job.applicants !== null
              ? t("jobBoard.row.applicants", { count: job.applicants })
              : "",
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
            className={`inline-flex h-[16px] items-center rounded-full border px-1.5 font-mono text-[9.5px] ${sourceClasses(job.source_adapter)}`}
          >
            {job.source_adapter}
          </span>
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        {/* Every job carries at least a keyword score (the instant on-device
            floor), so there is no "Pending"/"Score failed" state — an AI
            failure falls back to a grey keyword score. The muted "scoring…"
            only shows in the sub-second window before the first floor lands. */}
        {job.score ? (
          <span
            data-keyword={job.score.scorer_impl === "scorer-deterministic"}
            title={
              job.score.scorer_impl === "scorer-deterministic"
                ? t("jobBoard.row.keywordScoreTitle")
                : undefined
            }
            className={
              "font-mono text-[15px] font-semibold " +
              (job.score.scorer_impl === "scorer-deterministic"
                ? "text-ink-3"
                : (tier?.text ?? ""))
            }
          >
            {job.score.score_0_100}
          </span>
        ) : (
          <span
            data-testid="scoring-inflight"
            className="font-mono text-[10px] font-medium text-ink-4"
          >
            {t("jobBoard.row.scoring")}
          </span>
        )}
        {expired ? (
          <span
            data-testid="expired-label"
            className="inline-flex items-center rounded-full border border-border-2 bg-surface-2 px-1.5 py-0.5 text-[9.5px] font-medium text-ink-3"
          >
            {t("jobBoard.row.olderListing")}
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
  searchQ = "",
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
  searchQ?: string;
}) {
  const { t } = useTranslation();
  // Per-job automation toggles (US-JB-03), seeded from the Settings default
  // (auto-packet-on-save) and reset per job — prototype jobs.html semantics.
  const [toggles, setToggles] = useState({ ...packetDefaults });
  // Optimistic Save feedback (2026-07-11 #2): the POST is ~10 ms and every
  // on-Save op is queued async server-side — the button must not wait for the
  // refetch round-trip to acknowledge the click.
  const [justSaved, setJustSaved] = useState(false);
  // Row-level watchlist (approved-plan #4): a real toggle over the tracked-
  // companies roster — state derives from the watchlist itself, never from a
  // local flag, so it survives reload and matches the preferences modal.
  const watchCompany = useWatchCompany();
  const unwatchCompany = useUnwatchCompany();
  const { data: watchlist } = useWatchlist();
  const [watchError, setWatchError] = useState<Error | null>(null);
  const jobId = job?.id;
  useEffect(() => {
    setToggles({ ...packetDefaults });
    setJustSaved(false);
    setWatchError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset per job only
  }, [jobId]);
  // Match by board-root URL prefix OR by company (several ATSes publish
  // postings on the company's own domain — Greenhouse absolute_url — so the
  // job URL never contains the board root; same fallback the backend uses).
  const slugish = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  const watchedEntry = job
    ? watchlist?.find(
        (e) =>
          job.canonical_url === e.url ||
          job.canonical_url.startsWith(`${e.url}/`) ||
          (!!e.company && !!job.company && slugish(e.company) === slugish(job.company)),
      )
    : undefined;
  if (!job) {
    return (
      <div className="grid h-full place-items-center text-[12.5px] text-ink-3">
        {t("jobBoard.detail.pickJob")}
      </div>
    );
  }
  const subtitle = [
    job.company,
    job.location,
    workLabel(job.work_style) || t("jobBoard.detail.workStyleNA"),
    job.salary || t("jobBoard.detail.salaryNA"),
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
                "inline-flex h-[18px] items-center rounded-full border px-1.5 font-mono text-[10px] hover:opacity-80 " +
                sourceClasses(job.source_adapter) +
                (active ? " ring-2 ring-accent" : "")
              }
              title={t("jobBoard.detail.sourcePillTitle")}
            >
              {job.source_adapter}
            </button>
          </div>
          <div className="mt-1 text-[12.5px] text-ink-2">{subtitle}</div>
          <div className="text-[11.5px] text-ink-3">
            {t("jobBoard.detail.posted", { date: job.posted_at || t("jobBoard.detail.na") })}
          </div>
        </div>
        {job.score ? (
          <div className="flex flex-col items-center">
            <MatchRing
              score={job.score.score_0_100}
              keyword={job.score.scorer_impl === "scorer-deterministic"}
            />
            <span className="mt-1 text-[10.5px] text-ink-3">
              {job.score.scorer_impl === "scorer-deterministic"
                ? t("jobBoard.detail.keywordsScored")
                : t("jobBoard.detail.match")}
            </span>
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
          {job.saved || justSaved ? t("jobBoard.detail.saved") : t("jobBoard.detail.save")}
        </button>
        <button
          onClick={() => onRemove(job)}
          data-testid="remove-job"
          className="inline-flex h-[30px] min-w-[110px] items-center justify-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
        >
          <Icon name="trash" size={14} strokeWidth={2} />
          {t("jobBoard.detail.remove")}
        </button>
        {/* Per-job automation toggles (US-JB-03). Referrals only when the
            networking master toggle is on (US-NW-09 / FR-SET-03); the
            "Application form answers" toggle stays retired (applier.md §2). */}
        <div className="ml-2 flex items-center gap-1.5" data-testid="jd-automation-toggles">
          {(["resume", "cl", ...(networkingEnabled ? (["refs"] as const) : [])] as const).map(
            (slot) => {
              const on = toggles[slot];
              const label =
                slot === "resume" ? t("jobBoard.detail.toggleResume")
                : slot === "cl" ? t("jobBoard.detail.toggleCoverLetter")
                : t("jobBoard.detail.toggleFindReferrals");
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
            title={t("jobBoard.detail.restoreListingTitle")}
            className="inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
          >
            <Icon name="bookmark" size={14} strokeWidth={2} />
            {t("jobBoard.detail.restoreListing")}
          </button>
        ) : null}
        <div className="flex-1" />
        {watchError && watchError instanceof ApiError && watchError.status === 422 ? (
          // The board genuinely can't be watched (self-hosted portal, no
          // adapter) — say so, with the server's verbatim reason on hover.
          <span
            data-testid="watch-company-unsupported"
            title={watchError.message}
            className="inline-flex h-[30px] items-center px-3 text-[12px] text-ink-3"
          >
            {t("jobBoard.detail.cantWatchSource")}
          </span>
        ) : (
          <button
            data-testid="watch-company"
            data-on={!!watchedEntry}
            title={
              watchError
                ? t("jobBoard.detail.watchFailedTitle", { message: watchError.message })
                : watchedEntry
                  ? t("jobBoard.detail.watchOnTitle")
                  : t("jobBoard.detail.watchOffTitle")
            }
            disabled={watchCompany.isPending || unwatchCompany.isPending}
            onClick={() => {
              setWatchError(null);
              if (watchedEntry) {
                unwatchCompany.mutate(watchedEntry.url, {
                  onError: (e) => setWatchError(e instanceof Error ? e : new Error(String(e))),
                });
              } else {
                watchCompany.mutate(
                  { job_id: job.id },
                  {
                    onError: (e) => setWatchError(e instanceof Error ? e : new Error(String(e))),
                  },
                );
              }
            }}
            className={
              "inline-flex h-[30px] items-center gap-1.5 rounded-7 border px-2 text-[11.5px] font-medium disabled:opacity-70 " +
              (watchedEntry
                ? "border-accent bg-accent-wash text-accent-ink"
                : "border-border-2 bg-surface text-ink-3 hover:bg-surface-3")
            }
          >
            <span
              className={
                "relative inline-block h-3.5 w-6 rounded-full transition-colors " +
                (watchedEntry ? "bg-accent" : "bg-border-2")
              }
            >
              <span
                className={
                  "absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white transition-all " +
                  (watchedEntry ? "left-[13px]" : "left-0.5")
                }
              />
            </span>
            {/* Honest in-flight label (no optimistic pre-flip): almost every
                toggle settles in one fast round-trip — the label only lingers
                on a first-ever board probe. */}
            {/* "Show more jobs from this company" — outcome-first lingo,
                matching the roster's heading (maintainer 2026-07-22). */}
            {watchCompany.isPending
              ? t("jobBoard.detail.adding")
              : unwatchCompany.isPending
                ? t("jobBoard.detail.removing")
                : watchError
                  ? t("jobBoard.detail.failedRetry")
                  : t("jobBoard.detail.showMoreFromCompany")}
          </button>
        )}
        <a
          href={job.canonical_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-[30px] items-center gap-1 rounded-7 px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
        >
          {t("jobBoard.detail.openPosting")}
        </a>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {/* JD | Scoring as equal columns (maintainer, 2026-07-11) — the scoring
            table needs real width, matching the detail modal's Scoring tab. */}
        <div className="grid grid-cols-2 gap-6 px-6 py-5">
          {searchQ && job.description.toLowerCase().includes(searchQ.toLowerCase()) ? (
            // Active search hit inside this JD: render it plain with the
            // matches marked — seeing WHERE it matched beats markdown niceties
            // while a search is live (maintainer 2026-07-22).
            <div
              data-testid="jd-search-highlighted"
              className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-2"
            >
              {highlight(job.description, searchQ)}
            </div>
          ) : (
            <Markdown md={job.description} />
          )}
          <aside className="flex flex-col gap-4">
            <div className="rounded-lg border border-border bg-surface-2 p-4">
              <h3 className="mb-2 text-[12px] font-semibold text-ink-3">
                {job.score?.scorer_impl === "scorer-deterministic"
                  ? t("jobBoard.detail.keywordsScored")
                  : t("jobBoard.detail.matchScore")}
              </h3>
              {job.score?.scorer_impl === "scorer-deterministic" ? (
                <p className="mb-2 text-[11.5px] text-ink-3" data-testid="keyword-score-note">
                  {t("jobBoard.detail.keywordScoreNote")}
                </p>
              ) : null}
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
                <p className="text-[12px] text-ink-3">{t("jobBoard.detail.scoringInFlight")}</p>
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
  const { t } = useTranslation();
  const wrap = "grid h-full place-items-center px-6 text-center text-[12.5px] text-ink-3";
  // Filters or a search hid every row — distinct from a genuinely empty scrape.
  if (filteredOut) {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="filtered">
        {t("jobBoard.empty.filtered")}
      </div>
    );
  }
  const status = loading ? "running" : (meta?.scan_status ?? "empty");
  if (status === "running") {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-running">
        <div className="flex flex-col items-center gap-2">
          <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
          <div className="font-medium text-ink-2">{t("jobBoard.empty.scanning")}</div>
          <div data-testid="scrape-found-count">
            {t("jobBoard.empty.foundSoFar", { count: meta?.total ?? 0 })}
          </div>
        </div>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-error">
        <div className="flex flex-col items-center gap-1">
          <div className="font-medium text-bad">{t("jobBoard.empty.scrapeFailed")}</div>
          <div className="max-w-[360px] text-ink-3">
            {meta?.scan_error || t("jobBoard.empty.unknownError")}
          </div>
          <div className="text-ink-4">
            {t("jobBoard.empty.lastAttempt", { ago: shortAgo(meta?.last_scan_at) })}
          </div>
        </div>
      </div>
    );
  }
  // scrape-empty (genuinely zero rows) — offer the escape hatches.
  return (
    <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-empty">
      <div className="flex flex-col items-center gap-1">
        <div className="font-medium text-ink-2">{t("jobBoard.empty.noMatches")}</div>
        <div className="text-ink-3">
          {t("jobBoard.empty.widenPrefs")}
        </div>
        <div className="text-ink-4">
          {t("jobBoard.empty.lastRefresh", { ago: shortAgo(meta?.last_scan_at) })}
        </div>
      </div>
    </div>
  );
}

export function JobBoard() {
  const { t } = useTranslation();
  // Board search (FR-JB-13, consolidated 2026-07-22): ONE bar next to Sort,
  // deep all-text match (title/company/location + JD + score texts), matches
  // highlighted in the list and the open JD. Server-side (the feed is
  // paginated — filtering loaded pages client-side would miss matches on
  // unloaded pages), debounced per keystroke.
  const [textSearch, setTextSearch] = useState("");
  const textQ = useDebounced(textSearch.trim(), 250);
  const board = useBoard("", textQ);
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
  // After a master-resume edit in AI mode, ask before spending tokens to
  // re-score the board (maintainer 2026-07-23). Holds the server's preview of
  // the cache misses a confirmed run would enqueue, or null when hidden.
  const [rescoreAsk, setRescoreAsk] = useState<RescorePreview | null>(null);
  const qc = useQueryClient();
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
    if (status === "AI")
      list = list.filter((j) => j.score?.scorer_impl === "scorer-llm");
    if (status === "KEYWORD")
      list = list.filter((j) => j.score?.scorer_impl === "scorer-deterministic");
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
        <h1 className="text-[14px] font-semibold text-ink">{t("nav.jobBoard")}</h1>
        <div className="ml-auto flex items-center gap-3 py-1.5">
          {/* Master Resume before finder prefs (maintainer 2026-07-23 swap). */}
          <button
            onClick={() => setShowMaster(true)}
            data-action="open-master-resume"
            title={t("jobBoard.header.masterResumeTitle")}
            className="inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink"
          >
            <Icon name="file" size={14} strokeWidth={2} />
            {t("jobBoard.header.masterResume")}
          </button>
          <button
            onClick={() => setShowPrefs(true)}
            data-testid="finder-prefs"
            title={t("jobBoard.header.finderPrefsTitle")}
            className="inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink"
          >
            <Icon name="settings" size={14} strokeWidth={2} />
            {t("jobBoard.header.finderPrefs")}
          </button>
          <HeaderDeletedButton
            label={t("jobBoard.header.deletedJobs")}
            count={trashed.length}
            onClick={() => setShowTrash(true)}
            testid="trash-btn"
          />
          <HeaderAddButton
            label={t("jobBoard.header.addJobByUrl")}
            onClick={() => setShowAdd(true)}
            testid="add-job"
          />
        </div>
      </header>

      {/* Filter row — right-aligned like the Applications/Networking FilterBar,
          so the trailing Search box lands on the same edge in every tab
          (maintainer 2026-07-24 #4). */}
      <div
        data-testid="v2-filters"
        className="flex min-h-[45px] flex-wrap items-center justify-end gap-2 border-b border-border bg-surface px-5 py-2"
      >
        {/* Work style chip group (US-JB-02) — first, per the prototype filter row */}
        <div className="flex items-center gap-1.5" id="filter-ws" aria-label={t("jobBoard.filters.workStyle")}>
          <span className="text-[11.5px] text-ink-4">{t("jobBoard.filters.workStyle")}</span>
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
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] text-ink-4">{t("jobBoard.filters.posted")}</span>
          {[
            [0, "jobBoard.filters.anyTime"],
            [1, "jobBoard.filters.last24h"],
            [7, "jobBoard.filters.last7d"],
            [30, "jobBoard.filters.last30d"],
          ].map(([v, l]) => (
            <Chip key={v} active={posted === v} onClick={() => setPosted(v as number)}>
              {t(l as string)}
            </Chip>
          ))}
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] text-ink-4">{t("jobBoard.filters.minSalary")}</span>
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
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5" id="filter-status">
          <span className="text-[11.5px] text-ink-4">{t("jobBoard.filters.status")}</span>
          {(
            [
              ["ALL", "jobBoard.filters.all"],
              ["AI", "jobBoard.filters.aiScored"],
              ["KEYWORD", "jobBoard.filters.keywordsScored"],
            ] as [StatusFilter, string][]
          ).map(([s, label]) => (
            <Chip key={s} active={status === s} onClick={() => setStatus(s)}>
              {t(label)}
            </Chip>
          ))}
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
        <div className="flex items-center gap-1.5">
          <span className="text-[11.5px] text-ink-4">{t("jobBoard.filters.sort")}</span>
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
        </div>
        <span className="mx-1 h-4 w-px bg-border-2" />
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
      </div>

      {/* Split */}
      <div className="flex min-h-0 flex-1" data-testid="v2-split-host">
        <div className="flex min-w-0 flex-col border-r border-border" style={{ flexBasis: `${split}%` }}>
          <div className="flex items-center justify-between border-b border-border px-4 py-2 text-[12px] text-ink-3">
            <span data-testid="job-list-count">
              {/* Live total from the server, not the loaded/filtered count (FR-JB-02);
                  real "last refresh" from the last successful scan (FR-JB-10). */}
              {t("jobBoard.list.jobsCount", { count: meta?.total ?? visible.length })}{" "}
              <span className="text-ink-3/80" data-testid="last-refresh">
                {t("jobBoard.list.lastRefresh", { ago: shortAgo(meta?.last_scan_at) })}
              </span>
            </span>
          </div>
          <div
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
                filteredOut={boardJobs.length > 0 || Boolean(textQ)}
              />
            ) : (
              <>
                {visible.map((j) => (
                  <JobRow
                    key={j.id}
                    job={j}
                    selected={selected?.id === j.id}
                    onClick={() => setSelectedId(j.id)}
                    q={textQ}
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
                      {board.isFetchingNextPage ? t("jobBoard.list.loading") : t("jobBoard.list.loadMore")}
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
          onSaveMaster={(md: string) => {
            // Save the resume; scores are cached per resume version. Keyword
            // mode re-scores server-side for free at save; AI mode costs
            // tokens, so preview the cache misses and ask first (declining
            // keeps the prior scores visible — the board shows the latest
            // version). An unchanged save bumps nothing and asks nothing.
            void updateProfile.mutateAsync(md).then(async () => {
              if (settings?.scoring_mode === "llm") {
                const preview = await api.rescorePreview();
                if (preview.to_score > 0) {
                  setShowMaster(false); // close the editor so the prompt stands alone
                  setRescoreAsk(preview);
                  return;
                }
              }
              invalidateFeed(qc); // keyword mode already re-scored / nothing to score
            });
          }}
        />
      ) : null}
      {rescoreAsk !== null ? (
        <RescoreAiDialog
          preview={rescoreAsk}
          reason="resume-edit"
          onClose={() => setRescoreAsk(null)}
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
  const { t } = useTranslation();
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
              aria-label={t("jobBoard.prefs.removeAria")}
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
  display,
}: {
  options: string[];
  value: string;
  onChange: (v: string) => void;
  testid: string;
  /** Maps a persisted option VALUE to its displayed (translated) label. */
  display?: (v: string) => string;
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
          {display ? display(o) : o}
        </button>
      ))}
    </div>
  );
}

// Tracked companies (job-finder-preferences design 2026-07-21): the roster
// view of the `watched` [[sources]] rows the per-job "Watch company" action
// writes — same data, now listable/removable/addable from one place.
function TrackedCompanies() {
  const { t } = useTranslation();
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
        <h3 className="text-[13px] font-semibold text-ink">
          {t("jobBoard.prefs.tracked.heading")}
        </h3>
        <p className="text-[11.5px] text-ink-3">
          {t("jobBoard.prefs.tracked.hint")}
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
              onClick={() => {
                setError("");
                unwatch.mutate(e.url, {
                  // A failed removal was silently swallowed here — the ×
                  // just did nothing (maintainer 2026-07-22). Reuse the
                  // section's error line so the reason is visible.
                  onError: (err) =>
                    setError(
                      t("jobBoard.prefs.tracked.removeFailed", {
                        message: err instanceof Error ? err.message : String(err),
                      }),
                    ),
                });
              }}
              className="text-ink-3 hover:text-bad"
              aria-label={t("jobBoard.prefs.tracked.stopTrackingAria")}
              data-testid="fp-tracked-remove"
            >
              ×
            </button>
          </li>
        ))}
        {(entries ?? []).length === 0 ? (
          <li className="rounded-7 border border-dashed border-border-2 px-2.5 py-1.5 text-[12px] text-ink-3">
            {t("jobBoard.prefs.tracked.empty")}
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
          {watchCompany.isPending ? t("jobBoard.prefs.tracked.adding") : t("jobBoard.prefs.tracked.track")}
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

// "in 5h" / "in 2d" for the next-automatic-scan line; "overdue — any minute"
// when the tick hasn't caught up yet.
function nextScanLabel(iso: string): string {
  const mins = Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
  if (mins <= 0) return i18n.t("jobBoard.prefs.nextScanOverdue");
  if (mins < 60) return i18n.t("jobBoard.prefs.inMinutes", { n: mins });
  if (mins < 48 * 60) return i18n.t("jobBoard.prefs.inHours", { n: Math.round(mins / 60) });
  return i18n.t("jobBoard.prefs.inDays", { n: Math.round(mins / (24 * 60)) });
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
  const { t } = useTranslation();
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
  const [savedFlash, setSavedFlash] = useState(false);
  const { data: profile } = useProfile();
  const triggerScan = useTriggerScan();
  const masterName = firstHeading(profile?.master_md);
  const { data: schedules } = useSchedules();
  const scanSchedule = schedules?.find((s) => s.kind === "scan");

  // Autosave (maintainer 2026-07-22 #2): edits persist on their own, matching
  // the rest of the app's instant-persist controls (Settings toggles, the
  // Tracked companies roster right above). "Rescan now" only triggers the
  // scan — flushing any still-debouncing edit first so it never scans stale
  // values. The description carries the "auto saved" promise; the footer-left
  // "Changes saved!" is the per-edit confirmation.
  const snapshot = JSON.stringify({
    roles,
    locations,
    freshness,
    cadence,
    excludedCompanies,
    excludedKeywords,
  });
  const lastSaved = useRef(snapshot);

  async function persist(snap: string): Promise<void> {
    setSaveError("");
    // networking_enabled deliberately omitted — this modal never touches it.
    await api.savePreferences({
      role_aliases: roles,
      locations,
      freshness_days: FINDER_FRESHNESS_DAYS[freshness] ?? 7,
      scrape_cadence: cadence,
      excluded_companies: excludedCompanies,
      excluded_keywords: excludedKeywords,
    });
    lastSaved.current = snap;
    setSavedFlash(true);
    void qc.invalidateQueries({ queryKey: qk.settings });
    void qc.invalidateQueries({ queryKey: qk.schedules }); // next-scan line
    invalidateFeed(qc); // excludes hide board rows without waiting for a scan
  }

  const dirty = snapshot !== lastSaved.current;
  const valid = roles.length > 0 && locations.length > 0;
  useEffect(() => {
    if (!dirty || !valid) return;
    const t = setTimeout(() => {
      persist(snapshot).catch((e: unknown) =>
        setSaveError(e instanceof Error ? e.message : String(e)),
      );
    }, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- snapshot encodes every field
  }, [snapshot]);

  async function rescanNow() {
    setSaving(true);
    try {
      if (dirty && valid) await persist(snapshot);
      triggerScan.mutate(); // runs against the values just saved
      onClose();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  return (
    <Modal title={t("jobBoard.prefs.title")} onClose={onClose} width={560}>
      <form
        className="flex flex-col gap-5 px-5 py-5"
        onSubmit={(e) => {
          e.preventDefault();
          void rescanNow();
        }}
      >
        <p className="-mt-2 text-[12px] text-ink-3">
          {t("jobBoard.prefs.intro")}
        </p>
        <PrefChipInput
          label={t("jobBoard.prefs.roles.label")}
          hint={t("jobBoard.prefs.roles.hint")}
          items={roles}
          onAdd={(v) => setRoles((r) => (r.includes(v) ? r : [...r, v]))}
          onRemove={(v) => setRoles((r) => r.filter((x) => x !== v))}
          placeholder={t("jobBoard.prefs.roles.placeholder")}
          testid="fp-roles"
        />
        <PrefChipInput
          label={t("jobBoard.prefs.locations.label")}
          hint={t("jobBoard.prefs.locations.hint")}
          items={locations}
          onAdd={(v) => setLocations((r) => (r.includes(v) ? r : [...r, v]))}
          onRemove={(v) => setLocations((r) => r.filter((x) => x !== v))}
          placeholder={t("jobBoard.prefs.locations.placeholder")}
          testid="fp-locations"
        />
        <PrefChipInput
          label={t("jobBoard.prefs.excludedCompanies.label")}
          hint={t("jobBoard.prefs.excludedCompanies.hint")}
          items={excludedCompanies}
          onAdd={(v) => setExcludedCompanies((r) => (r.includes(v) ? r : [...r, v]))}
          onRemove={(v) => setExcludedCompanies((r) => r.filter((x) => x !== v))}
          placeholder={t("jobBoard.prefs.excludedCompanies.placeholder")}
          testid="fp-exclude-companies"
        />
        <PrefChipInput
          label={t("jobBoard.prefs.excludedKeywords.label")}
          hint={t("jobBoard.prefs.excludedKeywords.hint")}
          items={excludedKeywords}
          onAdd={(v) => setExcludedKeywords((r) => (r.includes(v) ? r : [...r, v]))}
          onRemove={(v) => setExcludedKeywords((r) => r.filter((x) => x !== v))}
          placeholder={t("jobBoard.prefs.excludedKeywords.placeholder")}
          testid="fp-exclude-keywords"
        />
        <TrackedCompanies />
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">{t("jobBoard.prefs.freshness.heading")}</h3>
            <p className="text-[11.5px] text-ink-3">
              {t("jobBoard.prefs.freshness.hint")}
            </p>
          </header>
          <RadioRow
            options={["24h", "7d", "30d", "Any"]}
            value={freshness}
            onChange={setFreshness}
            testid="fp-freshness"
            display={(v) => t(`jobBoard.prefs.freshness.values.${v}`)}
          />
        </section>
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">{t("jobBoard.prefs.cadence.heading")}</h3>
            <p className="text-[11.5px] text-ink-3">
              {t("jobBoard.prefs.cadence.hint")}
            </p>
          </header>
          <RadioRow
            options={["Every 6h", "Every 12h", "Every 24h", "Every 48h", "Every 72h"]}
            value={cadence}
            onChange={setCadence}
            testid="fp-cadence"
            display={(v) => t(`jobBoard.prefs.cadence.values.${v}`)}
          />
          {/* Proof the cadence is real (2026-07-22): the scan schedule's actual
              next firing time. Saving here also rescans now, which pushes this
              out one full interval — by design, to avoid a double scan. */}
          {scanSchedule ? (
            <p className="text-[11.5px] text-ink-3" data-testid="fp-next-scan">
              {scanSchedule.enabled
                ? t("jobBoard.prefs.nextScan", { when: nextScanLabel(scanSchedule.next_due_at) })
                : t("jobBoard.prefs.autoScanOff")}
            </p>
          ) : null}
        </section>
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">{t("jobBoard.prefs.master.heading")}</h3>
            <p className="text-[11.5px] text-ink-3">{t("jobBoard.prefs.master.hint")}</p>
          </header>
          <div className="flex items-center justify-between gap-3 rounded-7 border border-border-2 bg-surface-2 px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-[12.5px] font-medium text-ink">
                {masterName
                  ? t("jobBoard.prefs.master.named", { name: masterName })
                  : t("jobBoard.prefs.master.none")}
              </div>
              <div className="font-mono text-[11px] text-ink-3">
                {masterName ? t("jobBoard.prefs.master.ready") : t("jobBoard.prefs.master.addOne")}
              </div>
            </div>
            {/* View/Replace opens the master-resume popup — returns with the
                resume-popup commit. */}
          </div>
        </section>
        <div className="-mx-5 -mb-5 mt-2 flex items-center justify-end gap-2 border-t border-border bg-surface-2 px-5 py-3">
          {/* Footer-left: the autosave confirmation (maintainer's ss1 spot);
              an error takes its place — never both. */}
          {saveError ? (
            <span className="mr-auto text-[11.5px] text-bad" data-testid="finder-prefs-error">
              {saveError}
            </span>
          ) : savedFlash && !dirty ? (
            <span className="mr-auto text-[11.5px] text-good" data-testid="finder-prefs-saved">
              {t("jobBoard.prefs.saved")}
            </span>
          ) : dirty && valid ? (
            <span className="mr-auto text-[11.5px] text-ink-3">{t("jobBoard.prefs.saving")}</span>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2 hover:text-ink"
          >
            {t("jobBoard.prefs.close")}
          </button>
          <button
            type="submit"
            disabled={saving || !valid}
            data-testid="finder-prefs-save"
            title={t("jobBoard.prefs.rescanTitle")}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? t("jobBoard.prefs.starting") : t("jobBoard.prefs.rescanNow")}
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
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<"entry" | "fetching" | "editing">("entry");
  const [draft, setDraft] = useState<JobDraft | null>(null);
  // Set when the pasted URL was permanently deleted (tombstoned) — trash is
  // recoverable, a tombstone is final, so re-add is impossible (US-JB-07).
  const [tombstoned, setTombstoned] = useState(false);
  const preview = useJobPreview();
  // Watchlist path (approved-plan #4): the same paste box also accepts a
  // company careers URL — "watch" adds it as a permanent scan source.
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
    <Modal title={t("jobBoard.addModal.title")} onClose={onClose} width={520}>
      {phase === "entry" ? (
        <form
          className="flex flex-col gap-3 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            fetchDetails();
          }}
        >
          {/* One modal, one job (maintainer 2026-07-22): the "watch a whole
              board" path moved out — that's "Show more jobs from this company"
              on any job row, or the roster of the same name in preferences. */}
          <label className="text-[12.5px] text-ink-2">
            {t("jobBoard.addModal.pasteLabel")}
          </label>
          {tombstoned ? (
            <p
              data-testid="add-job-tombstoned"
              className="rounded-md border border-bad/40 bg-bad-wash px-3 py-2 text-[12px] text-bad"
            >
              {t("jobBoard.addModal.tombstoned")}
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
          <div className="mt-1 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
            >
              {t("jobBoard.addModal.cancel")}
            </button>
            <button
              type="submit"
              data-testid="add-job-fetch-btn"
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
            >
              {t("jobBoard.addModal.fetchDetails")}
            </button>
          </div>
        </form>
      ) : phase === "fetching" ? (
        <div className="grid place-items-center px-5 py-10 text-[13px] text-ink-3">
          <div className="flex items-center gap-2">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
            {t("jobBoard.addModal.fetching")}
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
            {url || t("jobBoard.addModal.noUrl")}{" "}
            <button
              type="button"
              onClick={() => setPhase("entry")}
              className="text-accent hover:underline"
            >
              {t("jobBoard.addModal.refetch")}
            </button>
          </div>
          <input
            value={draft?.title ?? ""}
            onChange={(e) => patch({ title: e.target.value })}
            placeholder={t("jobBoard.addModal.titlePlaceholder")}
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <input
            value={draft?.company ?? ""}
            onChange={(e) => patch({ company: e.target.value })}
            placeholder={t("jobBoard.addModal.companyPlaceholder")}
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <input
            value={draft?.location ?? ""}
            onChange={(e) => patch({ location: e.target.value })}
            placeholder={t("jobBoard.addModal.locationPlaceholder")}
            className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink"
          />
          <textarea
            value={draft?.description ?? ""}
            onChange={(e) => patch({ description: e.target.value })}
            placeholder={t("jobBoard.addModal.descriptionPlaceholder")}
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
              {t("jobBoard.addModal.cancel")}
            </button>
            <button
              type="submit"
              data-testid="add-job-submit-btn"
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
            >
              {t("jobBoard.addModal.submit")}
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
  const { t } = useTranslation();
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  return (
    <Modal
      title={t("jobBoard.trashModal.title")}
      onClose={onClose}
      width={520}
      footer={
        <div className="flex items-center justify-between gap-3 text-[11.5px] text-ink-3">
          <span>{t("jobBoard.trashModal.retention")}</span>
          {confirmEmpty ? (
            <span className="flex items-center gap-2">
              <span className="text-ink-2">
                {t("jobBoard.trashModal.emptyConfirm", { count: trashed.length })}
              </span>
              <button
                data-testid="trash-empty-confirm-btn"
                onClick={() => {
                  onEmpty();
                  setConfirmEmpty(false);
                }}
                className="rounded-md border border-bad/40 bg-bad px-2 py-1 font-medium text-white hover:opacity-90"
              >
                {t("jobBoard.trashModal.confirm")}
              </button>
              <button
                onClick={() => setConfirmEmpty(false)}
                className="rounded-md border border-border px-2 py-1 text-ink-2 hover:bg-surface-3"
              >
                {t("jobBoard.trashModal.cancel")}
              </button>
            </span>
          ) : (
            <button
              data-testid="trash-empty-btn"
              disabled={trashed.length === 0}
              onClick={() => setConfirmEmpty(true)}
              className="rounded-md border border-bad/40 px-2 py-1 text-bad hover:bg-bad-wash disabled:opacity-40 disabled:hover:bg-transparent"
            >
              {t("jobBoard.trashModal.emptyTrash")}
            </button>
          )}
        </div>
      }
    >
      <div data-testid="trash-modal" className="px-5 py-4">
        {trashed.length === 0 ? (
          <p className="text-[13px] text-ink-3">{t("jobBoard.trashModal.empty")}</p>
        ) : (
          <ul className="space-y-2">
            {trashed.map((j) => (
              <li key={j.id} className="flex items-center gap-3 rounded-md border border-border px-3 py-2">
                <div className="grid h-8 w-8 place-items-center rounded bg-surface-2 text-[11px] font-semibold text-ink-2">
                  {initials(j.company)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium text-ink">{j.title}</div>
                  <div className="text-[11px] text-ink-3">
                    {t("jobBoard.trashModal.removedRecently", { company: j.company })}
                  </div>
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
                      {t("jobBoard.trashModal.deleteForever")}
                    </button>
                    <button
                      onClick={() => setConfirmId(null)}
                      className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                    >
                      {t("jobBoard.trashModal.cancel")}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      data-testid="trash-undo-btn"
                      onClick={() => onUndo(j.id)}
                      className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                    >
                      {t("jobBoard.trashModal.undo")}
                    </button>
                    <button
                      data-testid="trash-delete-forever-btn"
                      title={t("jobBoard.trashModal.deleteForever")}
                      onClick={() => setConfirmId(j.id)}
                      className="rounded-md border border-bad/40 px-2 py-1 text-[11.5px] text-bad hover:bg-bad-wash"
                    >
                      {t("jobBoard.trashModal.deleteForever")}
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
