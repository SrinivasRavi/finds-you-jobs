// What the trust chip may say, in what order (url-less-jobs.md section 15.5):
// `trust_flags` becomes 1 label and sentences, worst first. A flag with no
// sentence here renders nothing, so no machine string reaches a user; the repost
// sentence claims only what the corpus measured.

import type { TFunction } from "i18next";

import type { Job } from "../../api/types";

const RED_FLAG = "red-flag-term:";

export type TrustRead = { label: string; lines: string[] };

/** `null` when there's nothing a user would act on. */
export function readTrust(job: Job, t: TFunction): TrustRead | null {
  const flags = job.trust_flags ?? [];
  const has = (p: string) => flags.some((f) => f.startsWith(p));
  const value = (p: string) => flags.find((f) => f.startsWith(p))?.slice(p.length) ?? "";

  const lines: string[] = [];
  let label = "";

  // Worst first: a scam posting wastes the user's data, a repost wastes a
  // tailoring call, a stale one wastes a wait.
  if (has(RED_FLAG)) {
    label ||= t("jobBoard.row.trust.labelRisk");
    lines.push(t("jobBoard.row.trust.redFlag", { term: value(RED_FLAG) }));
  }
  // From `job_duplicates`, so "They're different" clears it (url-less-jobs.md 9.6).
  if (job.duplicates > 0) {
    label ||= t("jobBoard.row.trust.labelReposted");
    lines.push(t("jobBoard.row.trust.reposted", { count: job.duplicates, company: job.company }));
  }
  if (flags.includes("stale-posting")) {
    label ||= t("jobBoard.row.trust.labelStale");
    lines.push(t("jobBoard.row.trust.stale"));
  }
  if (flags.includes("insecure-url")) {
    label ||= t("jobBoard.row.trust.labelInsecure");
    lines.push(t("jobBoard.row.trust.insecure"));
  }
  if (!job.canonical_url) {
    label ||= t("jobBoard.row.trust.labelUnverified");
    lines.push(t("jobBoard.row.unverifiedTitle"));
  }

  return lines.length ? { label, lines } : null;
}
