import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useExplainCost,
  useExplainJob,
  useJobFactors,
  useUnwatchCompany,
  useWatchCompany,
  useWatchlist,
} from "../../api/queries";
import { ApiError } from "../../api/real";
import { type Job, type WatchCompanyResult } from "../../api/types";
import { Avatar } from "../../shell/Avatar";
import { FactorBars } from "../../shell/FactorBars";
import { DuplicatePrompt } from "./DuplicatePrompt";
import { Icon } from "../../shell/icons";
import { Markdown } from "../../shell/Markdown";
import { AddLinkDialog, type AddLinkMode } from "./AddLinkDialog";
import { FollowCompanyDialog } from "./FollowCompanyDialog";
import { FollowResult, followLooksRight } from "./FollowResult";
import { sourceClasses, workLabel } from "../jobFormat";
import { MatchRing, highlight } from "./JobRow";

/** "Explain this one": 1 click, 1 job, 1 AI call, with the price on the button
 *  before it is pressed (vision.md, cost honesty).
 *
 *  The price is what THIS user's own AI scores have cost, measured off their
 *  ledger, because the app cannot know what a model charges and a figure from a
 *  price list would be wrong the first time somebody switched model. With no
 *  history it names the model instead of inventing a number. */
function ExplainButton({ job }: { job: Job }) {
  const { t } = useTranslation();
  const cost = useExplainCost(true);
  const explain = useExplainJob();
  const price = cost.data;
  const label =
    price?.avg_usd != null
      ? t("jobBoard.detail.explainPriced", { usd: price.avg_usd.toFixed(2) })
      : price?.model
        ? t("jobBoard.detail.explainOnModel", { model: price.model })
        : t("jobBoard.detail.explain");
  return (
    <div className="mb-3">
      <button
        data-testid="explain-this-one"
        disabled={explain.isPending || price?.ready === false}
        onClick={() => explain.mutate(job.id)}
        className="w-full rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12px] text-ink-2 hover:bg-surface-3 disabled:text-ink-4"
      >
        {explain.isPending ? t("jobBoard.detail.explainRunning") : label}
      </button>
      {price?.ready === false ? (
        <p className="mt-1 text-[11px] text-ink-3">{t("jobBoard.detail.explainNoEngine")}</p>
      ) : (
        <p className="mt-1 text-[11px] text-ink-3">{t("jobBoard.detail.explainHint")}</p>
      )}
    </div>
  );
}

export function JobDetail({
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
  // The per-axis bars. Only the open card asks, so a 50-row board never fans
  // out into 50 reads; the read itself is indexed and costs no model call.
  const factors = useJobFactors(job?.id ?? null);
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
  // What following this company's board found, shown under the actions so the
  // user can check the link (watch-company.md section 3).
  const [followResult, setFollowResult] = useState<WatchCompanyResult | null>(null);
  const [followOpen, setFollowOpen] = useState(false);
  const [linkMode, setLinkMode] = useState<AddLinkMode | null>(null);
  const jobId = job?.id;
  useEffect(() => {
    setToggles({ ...packetDefaults });
    setJustSaved(false);
    setWatchError(null);
    setFollowResult(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset per job only
  }, [jobId]);
  // Match by board-root URL prefix OR by company (several ATSes publish
  // postings on the company's own domain — Greenhouse absolute_url — so the
  // job URL never contains the board root; same fallback the backend uses).
  const slugish = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  const watchedEntry = job
    ? watchlist?.find(
        (e) =>
          (!!job.canonical_url &&
            (job.canonical_url === e.url || job.canonical_url.startsWith(`${e.url}/`))) ||
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
        <Avatar name={job.company} size={14} shape="md" tone="brand" decorative />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[17px] font-semibold leading-tight text-ink">{job.title}</h2>
            <button
              data-testid="source-adapter-pill"
              data-source={job.source_adapter}
              onClick={() => onToggleSource(job.source_adapter)}
              className={
                "inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10px] hover:opacity-80 " +
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
            <MatchRing score={job.score.score_0_100} method={job.score.method} />
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
            "Application form answers" toggle stays retired (applier-as-built.md section 2). */}
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
          // The app can't trace this job to a board (a search source, a
          // self-hosted portal). The remedy is the careers page, so offer it.
          <button
            type="button"
            data-testid="watch-company-unsupported"
            title={watchError.message}
            onClick={() => setFollowOpen(true)}
            className="inline-flex h-[30px] items-center px-3 text-[12px] text-ink-3 underline hover:text-ink"
          >
            {t("jobBoard.detail.cantWatchSource")}
          </button>
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
              // A job with no link can't be traced to a board: ask for the
              // careers page, in its own dialog, never as the job's link (D12).
              if (!job.canonical_url && !watchedEntry) {
                setFollowOpen(true);
                return;
              }
              if (watchedEntry) {
                unwatchCompany.mutate(watchedEntry.url, {
                  onError: (e) => setWatchError(e instanceof Error ? e : new Error(String(e))),
                });
              } else {
                watchCompany.mutate(
                  { job_id: job.id },
                  {
                    onSuccess: (r) => setFollowResult(r),
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
        {job.canonical_url ? (
          <a
            href={job.canonical_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-[30px] items-center gap-1 rounded-7 px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
          >
            {t("jobBoard.detail.openPosting")}
          </a>
        ) : (
          // Same position, same weight. This is the affordance the user looks
          // at most, so it's where "always ask for the URL" shows up (9.4).
          <button
            type="button"
            data-testid="add-link-open"
            title={t("jobBoard.detail.addLinkTitle")}
            onClick={() => setLinkMode("link")}
            className="inline-flex h-[30px] items-center gap-1 rounded-7 px-3 text-[12px] font-medium text-accent hover:bg-surface-3"
          >
            {t("jobBoard.detail.addLink")}
          </button>
        )}
      </div>
      {followResult && watchedEntry && !followLooksRight(followResult) ? (
        <div className="px-6 pt-2">
          <FollowResult result={followResult} onChange={() => setFollowOpen(true)} />
        </div>
      ) : null}
      {followOpen ? (
        <FollowCompanyDialog
          company={job.company}
          onClose={() => {
            setFollowOpen(false);
            setWatchError(null);
          }}
        />
      ) : null}
      {linkMode !== null ? (
        <AddLinkDialog
          jobId={job.id}
          jobLabel={`${job.title} · ${job.company}`}
          mode={linkMode}
          onClose={() => setLinkMode(null)}
          onAttached={() => setLinkMode(null)}
        />
      ) : null}

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        <div className="px-6 pt-4">
          <DuplicatePrompt job={job} />
        </div>
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
                  {/* The bars first (scoring.md 7.3): they are the only part of
                      this panel that every method fills, and the only part a
                      user can argue with axis by axis. The prose below is what
                      AI scoring adds on top. */}
                  {factors.data && factors.data.factors.length > 0 ? (
                    <div className="mb-3">
                      <FactorBars
                        factors={factors.data.factors}
                        weights={factors.data.weights}
                      />
                    </div>
                  ) : null}
                  {/* The cheap methods rank well and write nothing, so the one
                      job the user is about to spend an hour on can buy prose on
                      its own (scoring-build.md T13). Hidden once AI scoring has
                      already written it: there would be nothing to buy. */}
                  {job.score.method !== "llm" ? (
                    <ExplainButton job={job} />
                  ) : null}
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
