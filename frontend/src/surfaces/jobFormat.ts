// Shared formatting helpers ported from jobs.html (score tiers, initials,
// source-adapter pill colors, time-ago).

import type { Job, WorkStyle } from "../api/types";

/** Work-style display label (jobs.html workLabel). */
export function workLabel(ws: WorkStyle): string {
  const map: Record<string, string> = { REMOTE: "Remote", HYBRID: "Hybrid", ONSITE: "Onsite" };
  return map[ws] ?? "";
}

/** Score → color tier (jobs.html matchScoreBadge). */
export function scoreTier(score: number): { text: string; ring: string; wash: string } {
  if (score >= 85) return { text: "text-good", ring: "#059669", wash: "bg-good-wash" };
  if (score >= 70) return { text: "text-accent", ring: "#257697", wash: "bg-accent-wash" };
  if (score >= 55) return { text: "text-warn", ring: "#d97706", wash: "bg-warn-wash" };
  return { text: "text-bad", ring: "#dc2626", wash: "bg-bad-wash" };
}

export function initials(company: string): string {
  return company
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
}

/** Source-adapter pill classes (jobs.html sourceCls map). */
export function sourceClasses(adapter: string): string {
  const map: Record<string, string> = {
    greenhouse: "border-good-2 bg-good-wash text-good",
    lever: "border-accent bg-accent-wash text-accent-ink",
    ashby: "border-purple-2 bg-purple-wash text-purple",
    workable: "border-warn-2 bg-warn-wash text-warn",
    smartrecruiters: "border-good-2 bg-good-wash text-good",
    recruitee: "border-accent bg-accent-wash text-accent-ink",
    teamtailor: "border-purple-2 bg-purple-wash text-purple",
    personio: "border-warn-2 bg-warn-wash text-warn",
    careers: "border-border-2 bg-surface text-ink-2",
    ycombinator: "border-warn-2 bg-warn-wash text-warn",
    rss: "border-border-2 bg-surface text-ink-2",
  };
  return map[adapter] ?? "border-border-2 bg-surface text-ink-2";
}

export function timeAgo(iso: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const days = Math.round((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  return `${Math.round(days / 30)}mo ago`;
}

/** Best-effort min-USD salary from a fuzzy string, across currencies/formats
 *  (FR-JB-04). Returns `null` when nothing is parseable — the caller must NOT
 *  hide a `null` row (rank-don't-gate; an unparseable/absent salary is never
 *  silently filtered out by a comp band). Rough currency proxies only. */
export function salaryFloor(salary: string): number | null {
  if (!salary) return null;
  // Grab the first number with an optional magnitude suffix (k / L=lakh / M).
  const m = salary.match(/([\d][\d,.]*)\s*([KkMmLl]?)/);
  if (!m) return null;
  const n = parseFloat(m[1].replace(/,/g, ""));
  if (Number.isNaN(n)) return null;
  const unit = m[2].toLowerCase();
  const inr = /₹|inr|rs\b|lpa|lakh/i.test(salary);
  let amount = n;
  if (unit === "k") amount = n * 1_000;
  else if (unit === "m") amount = n * 1_000_000;
  else if (unit === "l") amount = n * 100_000; // lakh
  else if (!unit && n < 1000) amount = n * 1000; // bare "40" → 40k
  // Rough currency normalization to USD for cross-currency band matching.
  if (inr) amount = amount / 83; // ₹ → $ proxy
  else if (/€/.test(salary)) amount = amount * 1.08;
  else if (/£/.test(salary)) amount = amount * 1.27;
  return Math.round(amount);
}

/** Short relative time for the board's "last refresh" (FR-JB-02) — minutes/hours. */
export function shortAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const s = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function jobHasScore(j: Job): boolean {
  return j.score !== null;
}

/** First markdown H1 of a resume (the candidate name), or "" if none/empty. */
export function firstHeading(md: string | undefined): string {
  const line = (md ?? "").split("\n").find((l) => l.startsWith("# "));
  return line ? line.slice(2).trim() : "";
}
