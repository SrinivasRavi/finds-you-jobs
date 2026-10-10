// What the trust chip is allowed to say, and in what order (url-less-jobs.md
// section 15.5). `trust_flags` is a list of machine strings written at ingest;
// this turns it into 1 label plus a list of sentences, worst first.
//
// 2 rules it exists to enforce. A flag family with no sentence here renders
// nothing, so a raw `probable-duplicate:019f6ea2-…` can never reach a user.
// And the repost sentence claims only what the corpus measured: that batches
// like this get withdrawn, never that the employer meant to ghost anybody.

import type { TFunction } from "i18next";

import type { Job } from "../../api/types";

const DUPLICATE = "probable-duplicate:";
const CROSS_LISTING = "probable-cross-listing:";
const BATCH = "duplicate-batch:";
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
  if (has(DUPLICATE) || has(CROSS_LISTING)) {
    label ||= t("jobBoard.row.trust.labelReposted");
    const count = Number.parseInt(value(BATCH), 10);
    lines.push(
      Number.isFinite(count) && count > 0
        ? t("jobBoard.row.trust.reposted", { count, company: job.company })
        : t("jobBoard.row.trust.repostedUncounted"),
    );
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
