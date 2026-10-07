// The 2 provenance axes a job card carries (url-less-jobs.md section 9.3):
// where the job came from, and whether it has a posting link. The source chip
// already shipped and answers the first; these answer the second, plus the
// first reader `trust_flags` has ever had.
//
// Both axes are wanted because they disagree on purpose: a job pasted on
// Monday and promoted on Friday keeps `source_adapter = paste-text` and now
// has a URL, and both of those facts are true.

import { useTranslation } from "react-i18next";

import type { Job } from "../../api/types";

const CHIP =
  "inline-flex h-[16px] items-center rounded-full border px-1.5 text-[9.5px]";

export function NoLinkChip({ job, onClick }: { job: Job; onClick?: () => void }) {
  const { t } = useTranslation();
  if (job.canonical_url) return null;
  return (
    <span
      data-testid="no-link-chip"
      title={t("jobBoard.row.noLinkTitle")}
      onClick={
        onClick &&
        ((e) => {
          e.stopPropagation();
          onClick();
        })
      }
      className={`${CHIP} border-border-2 bg-surface-3 text-ink-3 ${onClick ? "cursor-pointer hover:border-accent" : ""}`}
    >
      {t("jobBoard.row.noLink")}
    </span>
  );
}

/** `trust_score` and `trust_flags` have been computed at ingest, stored and
 *  serialized to the frontend since the first scan, and rendered nowhere
 *  (section 8.4). This is their first reader, which also surfaces the
 *  cross-listing flags nobody has ever seen. */
export function TrustChip({ job }: { job: Job }) {
  const { t } = useTranslation();
  const unverified = !job.canonical_url;
  if (!unverified && job.trust_flags.length === 0) return null;
  const lines = [
    unverified ? t("jobBoard.row.unverifiedTitle") : "",
    ...job.trust_flags,
  ].filter(Boolean);
  return (
    <span
      data-testid="trust-chip"
      title={lines.join("\n")}
      aria-label={t("jobBoard.row.unverified")}
      className={`${CHIP} border-warn/40 bg-warn/5 text-warn`}
    >
      ?
    </span>
  );
}

export function JobProvenanceChips({ job, onNoLink }: { job: Job; onNoLink?: () => void }) {
  return (
    <>
      <NoLinkChip job={job} onClick={onNoLink} />
      <TrustChip job={job} />
    </>
  );
}
