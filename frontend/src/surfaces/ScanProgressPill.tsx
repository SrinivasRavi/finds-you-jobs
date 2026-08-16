// Board-level Rescan status pill (observed-issue #2). After a scan is triggered
// (manually via "Rescan now", or on the background cadence), the Job Board shows
// one compact status: a round green sweeping ring + "Scanning for new roles…"
// while discovery runs, then a round green progress ring + "AI is scoring the newly
// found jobs" with an "M of N" count while the found jobs are AI-scored, and
// finally a round green check + "Scan complete. Found N new roles." that stays
// until the next scan supersedes it (no auto-dismiss — indefinite for now).
//
// Only the Job Board carries this board-level indicator — Applications and
// Networking surface progress per card. When nothing is (or was) running, the
// pill renders nothing (the last-scan time is surfaced elsewhere).

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useScanProgress } from "../api/queries";
import type { ScanProgress } from "../api/types";
import { Icon } from "../shell/icons";

/** True on the active→idle edge of a scan/scoring cycle — i.e. the moment to
 *  flip the pill to "Done". Fires whenever a cycle we SAW run settles, even if
 *  it found no new jobs (a re-scan that dedupes to zero still "completed" and
 *  deserves the confirmation check). Pure + exported so the edge detection is
 *  unit-testable: it must fire once on the active→idle transition, and must NOT
 *  fire on an idle-on-mount read (nothing ran) or while a cycle is still
 *  active. */
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
