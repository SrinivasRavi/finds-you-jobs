// The board's Rescan status pill: a sweeping ring while discovery runs, a
// progress ring with "M of N" while the new jobs are AI-scored, then a check with
// "Found N new roles" until the next scan. Only the board has it; the other tabs
// show progress per card. Renders nothing when no scan ran.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useScanProgress } from "../api/queries";
import type { ScanProgress } from "../api/types";
import { Icon } from "../shell/icons";

/** True on a scan cycle's active-to-idle edge, the moment to show "Done", even
 *  when it found nothing new. Never on an idle read at mount or mid-cycle.
 *  Exported for its unit test. */
export function isScanDoneEdge(
  wasActive: boolean,
  p: Pick<ScanProgress, "scan_running" | "score_pending">,
): boolean {
  const active = p.scan_running || p.score_pending > 0;
  return wasActive && !active;
}

export function ScanProgressPill() {
  const { t } = useTranslation();
  const { data } = useScanProgress();
  // Local edge state: only show "Done" when a cycle we SAW run has just settled,
  // never on the initial idle mount. The confirmation stays until the next scan
  // supersedes it (no auto-dismiss). `doneCount` freezes the roles-found count
  // captured at the settle edge, so the message is stable while it lingers.
  const [showDone, setShowDone] = useState(false);
  const [doneCount, setDoneCount] = useState(0);
  const wasActive = useRef(false);

  useEffect(() => {
    if (!data) return;
    const active = data.scan_running || data.score_pending > 0;
    if (isScanDoneEdge(wasActive.current, data)) {
      setDoneCount(data.new_found);
      setShowDone(true);
    } else if (active) {
      // A fresh cycle supersedes any lingering "Done".
      setShowDone(false);
    }
    wasActive.current = active;
  }, [data]);

  if (!data) return null;
  const active = data.scan_running || data.score_pending > 0;
  // Idle and nothing just finished → the pill is absent entirely.
  if (!active && !showDone) return null;

  const scoring = !data.scan_running && data.score_pending > 0;
  const done = !active && showDone;
  const pct = data.new_found > 0 ? Math.min(1, data.score_done / data.new_found) : 0;

  return (
    <div
      data-testid="scan-progress"
      data-state={data.scan_running ? "scanning" : scoring ? "scoring" : "done"}
      aria-live="polite"
      className="inline-flex items-center gap-2 text-[12px] font-medium italic text-ink-3"
    >
      {done ? (
        // Round dimmed-green check, sized to match the spinner/ring it replaces.
        <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-scan text-white">
          <Icon name="check" size={12} strokeWidth={3} />
        </span>
      ) : scoring ? (
        // Determinate green ring — fills as score_done climbs toward new_found.
        <span
          className="grid h-5 w-5 shrink-0 place-items-center rounded-full"
          style={{ background: `conic-gradient(var(--color-scan) ${pct * 360}deg, var(--surface-3) 0deg)` }}
          aria-hidden="true"
        >
          <span className="h-3 w-3 rounded-full bg-surface" />
        </span>
      ) : (
        // Indeterminate green ring — discovery has no known total, so the arc
        // sweeps around the track (see .fyj-scan-ring) rather than tracking a
        // count; visually consistent with the determinate scoring ring above.
        <span
          className="fyj-scan-ring grid h-5 w-5 shrink-0 place-items-center rounded-full"
          aria-hidden="true"
        >
          <span className="h-3 w-3 rounded-full bg-surface" />
        </span>
      )}
      <span>
        {data.scan_running
          ? `${t("jobBoard.scanProgress.scanning")}…`
          : scoring
            ? t("jobBoard.scanProgress.scoring")
            : t("jobBoard.scanProgress.done", { count: doneCount })}
      </span>
      {scoring ? (
        <span className="text-[11px] text-ink-3" data-testid="scan-progress-count">
          {t("jobBoard.scanProgress.count", { done: data.score_done, found: data.new_found })}
        </span>
      ) : null}
    </div>
  );
}
