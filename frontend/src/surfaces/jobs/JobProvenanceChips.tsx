// The 2 provenance axes on a job card (url-less-jobs.md section 9.3): where it
// came from (the source chip) and whether it has a posting link, plus the trust
// chip. They disagree on purpose: a job pasted Monday and given a link Friday is
// still `paste-text`, and has a URL.

import { useTranslation } from "react-i18next";

import type { Job } from "../../api/types";
import { readTrust } from "./trustRead";

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

/** The trust chip: the scam, repost, stale and unverified signals (section 8.4).
 *  It says the worst thing it knows, in words, and `readTrust` owns both the
 *  severity order and the rule that an untranslated flag family says nothing
 *  (section 15.5). A job whose only flags are scraper notes renders no chip. */
export function TrustChip({ job }: { job: Job }) {
  const { t } = useTranslation();
  const read = readTrust(job, t);
  if (!read) return null;
  return (
    <span
      data-testid="trust-chip"
      title={read.lines.join("\n\n")}
      aria-label={read.lines.join(" ")}
      className={`${CHIP} border-warn/40 bg-warn/5 text-warn`}
    >
      {read.label}
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
