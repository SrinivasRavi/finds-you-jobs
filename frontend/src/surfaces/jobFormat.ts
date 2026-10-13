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

/** The 4 scoring methods, in the vocabulary `Job.score_method` stores.
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

/** The outer arc's colour: red at 0, amber at 50, green at 100, interpolated
 *  from the `--bad`/`--warn`/`--good` values (inlined: a gradient stop is a colour,
 *  not a class). Continuous, because a 69 and a 70 aren't different kinds of job. */
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

/** Whole days from `iso` until now (D-F11). `mode` is explicit everywhere:
 *  "floor" counts completed days (days in column), "round" snaps to the nearest
 *  (time-ago copy). NaN for an unparseable timestamp; callers guard. */
export function daysBetween(iso: string, mode: "floor" | "round" = "floor"): number {
  const days = (Date.now() - new Date(iso).getTime()) / 86_400_000;
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

/** A relative-time phrase as an i18n key plus its number. Both formatters below
 *  used to return English, which the board spliced into translated sentences
 *  ("पिछला रिफ़्रेश 5m ago"). The 2026-07-24 extraction missed them because the
 *  literals came out of a helper, not JSX. Same shape as Networking.syncedAgo. */
export interface RelativeTime {
  key: string;
  n?: number;
}

/** Translate what the 2 formatters below return. `null` renders nothing. */
export function relativeTime(rt: RelativeTime | null): string {
  if (!rt) return "";
  return i18n.t(rt.key, rt.n === undefined ? undefined : { n: rt.n });
}

/** A posting's age, for the board row. `null` when there is no usable date. */
export function timeAgo(iso: string): RelativeTime | null {
  if (!iso) return null;
  const days = daysBetween(iso, "round");
  if (Number.isNaN(days)) return null;
  if (days <= 0) return { key: "relativeTime.today" };
  if (days < 30) return { key: "relativeTime.days", n: days };
  return { key: "relativeTime.months", n: Math.round(days / 30) };
}

/** Short relative time for the board's "last refresh" (FR-JB-02). Never null:
 *  "never" is a real answer here, unlike a missing posting date. */
export function shortAgo(iso: string | null | undefined): RelativeTime {
  if (!iso) return { key: "relativeTime.never" };
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return { key: "relativeTime.never" };
  const s = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (s < 60) return { key: "relativeTime.justNow" };
  const m = Math.round(s / 60);
  if (m < 60) return { key: "relativeTime.minutes", n: m };
  const h = Math.round(m / 60);
  if (h < 24) return { key: "relativeTime.hours", n: h };
  return { key: "relativeTime.days", n: Math.round(h / 24) };
}

export function jobHasScore(j: Job): boolean {
  return j.score !== null;
}

/** First markdown H1 of a resume (the candidate name), or "" if none/empty. */
export function firstHeading(md: string | undefined): string {
  const line = (md ?? "").split("\n").find((l) => l.startsWith("# "));
  return line ? line.slice(2).trim() : "";
}
