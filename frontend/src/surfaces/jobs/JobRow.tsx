import { memo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useRescoreJob } from "../../api/queries";
import { type Job } from "../../api/types";
import { Avatar } from "../../shell/Avatar";
import { JobProvenanceChips } from "./JobProvenanceChips";
import {
  methodColor,
  relativeTime,
  scoreRingColor,
  scoreTier,
  sourceClasses,
  timeAgo,
  workLabel,
} from "../jobFormat";

/** 2 concentric rings: the outer arc is the score, red through green, drawn as
 *  far round as the number; the inner ring is the method that produced it, so 2
 *  methods' numbers can be told apart at a glance. Keyword keeps its grey dashed
 *  circle: the free floor must never read as a paid score. */
export function MatchRing({ score, method }: { score: number; method: string }) {
  const { t } = useTranslation();
  const label = t(`scoreMethod.${method}`, { defaultValue: method });
  const title = t("scoreMethod.scoredBy", { method: label });
  if (method === "keyword") {
    return (
      <div
        className="grid h-12 w-12 place-items-center rounded-full border-2 border-dashed border-border-2 bg-surface-2"
        data-testid="keyword-score-ring"
        data-method="keyword"
        title={title}
      >
        <span className="text-[13px] font-semibold text-ink-3">{score}</span>
      </div>
    );
  }
  const tier = scoreTier(score);
  const deg = (score / 100) * 360;
  return (
    <div
      className="grid h-12 w-12 place-items-center rounded-full"
      data-testid="match-score-ring"
      data-method={method}
      title={title}
      style={{ background: `conic-gradient(${scoreRingColor(score)} ${deg}deg, var(--surface-3) 0deg)` }}
    >
      {/* An inset shadow rather than a border: a border would shrink the disc
          and reflow the number off centre. */}
      <div
        className="grid h-9 w-9 place-items-center rounded-full bg-surface"
        style={{ boxShadow: `inset 0 0 0 2px ${methodColor(method)}` }}
      >
        <span className={`text-[13px] font-semibold ${tier.text}`}>{score}</span>
      </div>
    </div>
  );
}

/** Rescore and the rescore-due mark, beside the score in the job pane
 *  (score-storage.md 5.2). Nothing rescores on its own. */
export function RescoreControl({ job }: { job: Job }) {
  const { t } = useTranslation();
  const rescore = useRescoreJob();
  return (
    <span className="mt-1 inline-flex items-center gap-1 text-[10.5px]">
      {job.score?.stale ? (
        <span
          data-testid="score-stale"
          title={t("jobBoard.row.staleTitle")}
          aria-label={t("jobBoard.row.staleTitle")}
          className="text-warn"
        >
          ⚠
        </span>
      ) : null}
      <button
        type="button"
        data-testid="rescore-job"
        title={t("jobBoard.row.rescoreTitle")}
        disabled={rescore.isPending}
        onClick={() => rescore.mutate(job.id)}
        className={rescore.isPending ? "text-ink-4" : "text-ink-3 hover:text-accent"}
      >
        {rescore.isPending ? t("jobBoard.row.rescoring") : t("jobBoard.row.rescore")}
      </button>
    </span>
  );
}

// Search-match highlighting (maintainer 2026-07-22): wherever the board search
// found text, show it — list rows and the open JD alike.
export function highlight(text: string, q: string): ReactNode {
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

// Memoized (F-M4): a 1000+-row feed re-ran every row's highlight() scans on
// each keystroke/click. Props stay memo-friendly — `job` is referentially
// stable per fetch (boardJobs/visible only rebuild the arrays), `onSelect` is
// the setState function itself, `q` is the debounced string. useTranslation
// inside still re-renders rows on a language switch.
export const JobRow = memo(function JobRow({
  job,
  selected,
  onSelect,
  q = "",
  onLinkFilter,
}: {
  job: Job;
  selected: boolean;
  onSelect: (id: string) => void;
  q?: string;
  onLinkFilter?: () => void;
}) {
  const { t } = useTranslation();
  const tier = job.score ? scoreTier(job.score.score_0_100) : null;
  const expired = job.board_state === "expired";
  return (
    <button
      onClick={() => onSelect(job.id)}
      data-testid="job-row"
      data-expired={expired}
      data-score-status={job.score_status}
      className={
        "flex w-full items-start gap-3 border-b border-border px-4 py-3 text-left transition-colors " +
        (expired ? "opacity-55 " : "") +
        (selected ? "bg-accent-wash/50" : "hover:bg-surface-2")
      }
    >
      <Avatar name={job.company} size={10} shape="md" tone="brand" decorative />
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
        {/* salary · time-ago */}
        <div className="text-[11.5px] text-ink-3">
          {[
            job.salary,
            relativeTime(timeAgo(job.posted_at)),
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
        {/* source pill (US-JB-10 informational variant) */}
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span
            className={`inline-flex h-[16px] items-center rounded-full border px-1.5 text-[9.5px] ${sourceClasses(job.source_adapter)}`}
          >
            {job.source_adapter}
          </span>
          {/* The URL axis, and the first thing to render trust_flags (9.3). */}
          <JobProvenanceChips job={job} onNoLink={onLinkFilter} />
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        {/* Every job carries at least a keyword score (the instant on-device
            floor), so there is no "Pending"/"Score failed" state — an AI
            failure falls back to a grey keyword score. The muted "scoring…"
            only shows in the sub-second window before the first floor lands.
            An `unscorable` job's grey 0 means "no description captured", not
            "rated 0", so it says so on hover (S-C24 D5). */}
        {job.score ? (
          <span
            data-keyword={job.score.scorer_impl === "scorer-deterministic"}
            data-method={job.score.method}
            title={
              job.score_status === "unscorable"
                ? t("jobBoard.row.unscorableTitle")
                : job.score.scorer_impl === "scorer-deterministic"
                  ? t("jobBoard.row.keywordScoreTitle")
                  : t("scoreMethod.scoredBy", {
                      method: t(`scoreMethod.${job.score.method}`, {
                        defaultValue: job.score.method,
                      }),
                    })
            }
            className={
              "inline-flex items-center gap-1.5 text-[15px] font-semibold " +
              (job.score.scorer_impl === "scorer-deterministic"
                ? "text-ink-3"
                : (tier?.text ?? ""))
            }
          >
            {/* The row's method mark. The detail pane draws the method as the
                dial's inner ring; a list row has no room for a dial, and
                without something here a board carrying 2 methods' numbers is
                unreadable while scrolling. Same colours, same hover line. */}
            <span
              aria-hidden="true"
              className="size-[7px] shrink-0 rounded-full"
              style={{ background: methodColor(job.score.method) }}
            />
            {job.score.score_0_100}
          </span>
        ) : (
          <span
            data-testid="scoring-inflight"
            className="text-[10px] font-medium text-ink-4"
          >
            {t("jobBoard.row.scoring")}
          </span>
        )}
        {expired ? (
          <span
            data-testid="expired-label"
            title={t("jobBoard.row.olderListingTitle")}
            className="inline-flex items-center rounded-full border border-border-2 bg-surface-2 px-1.5 py-0.5 text-[9.5px] font-medium text-ink-3"
          >
            {t("jobBoard.row.olderListing")}
          </span>
        ) : null}
      </div>
    </button>
  );
});
