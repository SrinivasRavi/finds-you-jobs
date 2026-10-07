// Shared formatting helpers ported from jobs.html (score tiers, source-adapter
// pill colors, time-ago). The initials rule moved to shell/Avatar.tsx with the
// avatar markup it exists to fill (duplication audit D-F7).

import type { Job, WorkStyle } from "../api/types";
import i18n from "../i18n";

/** Work-style display label (jobs.html workLabel). */
export function workLabel(ws: WorkStyle): string {
  const map: Record<string, string> = {
    REMOTE: i18n.t("shell.work.remote"),
    HYBRID: i18n.t("shell.work.hybrid"),
    ONSITE: i18n.t("shell.work.onsite"),
  };
  return map[ws] ?? "";
}

/** Score → color tier (jobs.html matchScoreBadge). */
export function scoreTier(score: number): { text: string; ring: string; wash: string } {
  if (score >= 85) return { text: "text-good", ring: "#059669", wash: "bg-good-wash" };
  if (score >= 70) return { text: "text-accent", ring: "#257697", wash: "bg-accent-wash" };
  if (score >= 55) return { text: "text-warn", ring: "#d97706", wash: "bg-warn-wash" };
  return { text: "text-bad", ring: "#dc2626", wash: "bg-bad-wash" };
}

/** Whole days elapsed from `iso` until now (duplication audit D-F11 — the
 *  hand-rolled copies of this arithmetic disagreed on floor vs round, so the
 *  same age could read "3d" on one surface and "4d" on another).
 *
 *  `mode` stays explicit at every call site because each existing choice is
 *  load-bearing: "floor" counts completed days (days-in-column / days-in-status),
 *  "round" snaps to the nearest day (relative time-ago copy), and "exact" keeps
 *  the fraction for comparisons that feed a filter rather than a label.
 *  Returns NaN for an unparseable timestamp — callers guard. */
/** The 4 scoring methods, in the vocabulary `Job.llm_score_method` stores.
 *  Theme tokens, not hexes, so both themes are right for free. */
export const SCORE_METHODS = ["llm", "jev", "local", "keyword"] as const;
export type ScoreMethod = (typeof SCORE_METHODS)[number];

const METHOD_COLOR: Record<ScoreMethod, string> = {
  llm: "var(--purple)",
  jev: "var(--accent)",
  local: "var(--pink)",
  keyword: "var(--ink-4)",
};

/** The inner ring's colour: which method produced the number. An unknown
 *  string (a newer sidecar naming a method this build doesn't know) reads as
 *  keyword grey rather than throwing — the board must never fail to draw. */
export function methodColor(method: string): string {
  return METHOD_COLOR[method as ScoreMethod] ?? METHOD_COLOR.keyword;
}

/** The outer arc's colour: red at 0, amber at 50, green at 100, interpolated.
 *  The 3 stops are the same `--bad`/`--warn`/`--good` values `scoreTier` uses,
 *  inlined for the same reason that function inlines them: a conic-gradient
 *  stop is a colour, not a class.
 *
 *  Continuous rather than banded (maintainer, 2026-10-07): a 69 and a 70 are
 *  not different kinds of job, and the old banding put a blue at 70 to 84, so
 *  the ramp read green, blue, amber, red going down. */
export function scoreRingColor(score: number): string {
  const stops: [number, [number, number, number]][] = [
    [0, [220, 38, 38]],
    [50, [217, 119, 6]],
    [100, [5, 150, 105]],
  ];
  const s = Math.max(0, Math.min(100, score));
  const i = s <= stops[1][0] ? 0 : 1;
  const [from, a] = stops[i];
  const [to, b] = stops[i + 1];
  const t = (s - from) / (to - from);
  const mix = a.map((v, k) => Math.round(v + (b[k] - v) * t));
  return `rgb(${mix[0]} ${mix[1]} ${mix[2]})`;
}

export function daysBetween(iso: string, mode: "floor" | "round" | "exact" = "floor"): number {
  const days = (Date.now() - new Date(iso).getTime()) / 86_400_000;
  if (mode === "exact") return days;
  return mode === "round" ? Math.round(days) : Math.floor(days);
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
  const days = daysBetween(iso, "round");
  if (Number.isNaN(days)) return "";
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
