// A score's reasons and factor evidence in the user's language (audit section 5).
// The keyword scorer sends a key and its facts beside the English; LLM scoring
// writes prose, and a score stored before this change keeps its English text.

import type { TFunction } from "i18next";

/** A line as its key and facts; `text` is the English the sidecar stored. */
export interface CodedLine {
  key: string;
  args: Record<string, unknown>;
  text?: string;
}

export type ScoreReason = string | CodedLine;

const isCoded = (v: unknown): v is CodedLine =>
  !!v && typeof v === "object" && typeof (v as CodedLine).key === "string";

/** `v` as a coded line, or null when it isn't one. */
export const codedLine = (v: unknown): CodedLine | null => (isCoded(v) ? v : null);

/** Every reason the sidecar sent, kept as it came: a string or a coded line. */
export function asReasons(raw: unknown): ScoreReason[] {
  return Array.isArray(raw)
    ? raw.filter((r): r is ScoreReason => typeof r === "string" || isCoded(r))
    : [];
}

/** 1 line, worded: a nested line (the location's "why") is worded first, and a
 *  missing number reads "unstated". */
export function scoreText(line: ScoreReason | null | undefined, t: TFunction): string {
  if (line == null) return "";
  if (typeof line === "string") return line;
  const args = Object.fromEntries(
    Object.entries(line.args ?? {}).map(([k, v]) => [
      k,
      isCoded(v) ? scoreText(v, t) : v == null ? t("scoreReason.unstated") : v,
    ]),
  );
  return t(`scoreReason.${line.key}`, { ...args, defaultValue: line.text ?? line.key });
}
