// The per-axis breakdown behind a score (scoring.md section 7.3), the same for
// all 4 methods. A probability (Jev's 2 confidence axes) is drawn hollow and never
// shows a weight, since confidence times weight means nothing. The weight shows
// beside each graded bar, and the evidence shows, so a bar can be argued with.

import { useTranslation } from "react-i18next";

/** One axis as a scorer judged it. Mirrors `scorer/types.py:Factor`. */
export interface ScoreFactor {
  factor: string;
  value: number;
  kind: string;
  evidence: string;
}

/** Display order, matching `scorer/output_parse.py:FACTOR_KEYS` so a bar chart
 *  and a parsed breakdown never disagree about which axis comes first. */
export const FACTOR_ORDER = [
  "hard_requirements",
  "core_stack",
  "years",
  "level",
  "location_preference",
  "logistics",
  "nice_to_haves",
] as const;

function order(factor: string): number {
  const at = (FACTOR_ORDER as readonly string[]).indexOf(factor);
  // An axis this build has never heard of sorts last rather than vanishing.
  return at === -1 ? FACTOR_ORDER.length : at;
}

function Bar({ factor, weight }: { factor: ScoreFactor; weight?: number }) {
  const { t } = useTranslation();
  const probability = factor.kind === "probability";
  const pct = Math.max(0, Math.min(1, factor.value));
  const name = t(`scoreFactor.${factor.factor}`, { defaultValue: factor.factor });
  return (
    <li data-testid={`factor-${factor.factor}`} data-kind={factor.kind} className="min-w-0">
      <div className="flex items-baseline justify-between gap-2 text-[11.5px]">
        <span className="truncate text-ink-2">{name}</span>
        <span className="shrink-0 tabular-nums text-ink-3">
          {probability
            ? t("scoreFactor.confidence", { percent: Math.round(pct * 100) })
            : Math.round(pct * 100)}
          {/* Rule 2. A probability never carries one, so its absence is the
              signal rather than an omission. */}
          {!probability && weight !== undefined ? (
            <span className="ml-1.5 text-ink-4" data-testid={`weight-${factor.factor}`}>
              {t("scoreFactor.weight", { percent: Math.round(weight * 100) })}
            </span>
          ) : null}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
        <div
          data-testid={`fill-${factor.factor}`}
          className={
            "h-full rounded-full " +
            // Rule 1. Hollow and dashed for a confidence, solid for a position.
            (probability ? "border border-dashed border-accent bg-accent-wash" : "bg-accent")
          }
          style={{ width: `${pct * 100}%` }}
        />
      </div>
      {factor.evidence ? (
        <p className="mt-1 text-[10.5px] leading-snug text-ink-3">{factor.evidence}</p>
      ) : null}
    </li>
  );
}

export function FactorBars({
  factors,
  weights,
}: {
  factors: ScoreFactor[];
  /** The weight table the total used, per axis. Omitted axes show no weight. */
  weights?: Record<string, number>;
}) {
  const { t } = useTranslation();
  if (factors.length === 0) {
    return <p className="text-[11.5px] text-ink-3">{t("scoreFactor.none")}</p>;
  }
  const sorted = [...factors].sort((a, b) => order(a.factor) - order(b.factor));
  return (
    <ul data-testid="factor-bars" className="space-y-2.5">
      {sorted.map((f) => (
        <Bar key={f.factor} factor={f} weight={weights?.[f.factor]} />
      ))}
    </ul>
  );
}
