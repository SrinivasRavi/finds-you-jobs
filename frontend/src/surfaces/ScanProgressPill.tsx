// Board-level Rescan status pill (observed-issue #2). After a scan is triggered
// (manually via "Rescan now", or on the background cadence), the Job Board shows
// one compact status: a round green spinner + "Scanning for new roles" while
// discovery runs, then a round green progress ring + "AI is scoring the newly
// found jobs" with an "M of N" count while the found jobs are AI-scored, and
// finally a round green check + "Done" for a few seconds before it dismisses.
//
// Only the Job Board carries this board-level indicator — Applications and
// Networking surface progress per card. When nothing is (or was) running, the
// pill renders nothing (the last-scan time is surfaced elsewhere).

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useScanProgress } from "../api/queries";
import type { ScanProgress } from "../api/types";
import { Icon } from "../shell/icons";

// How long the "Done" confirmation lingers before auto-dismissing.
const DONE_LINGER_MS = 4000;

/** True on the active→idle edge of a scan/scoring cycle that actually found
 *  jobs — i.e. the moment to flip the pill to "Done". Pure + exported so the
 *  edge detection is unit-testable: it must fire once when a cycle that was
 *  active goes idle with `new_found > 0`, and must NOT fire on an idle-on-mount
 *  read (nothing ran) or while a cycle is still active. */
export function isScanDoneEdge(
  wasActive: boolean,
  p: Pick<ScanProgress, "scan_running" | "score_pending" | "new_found">,
): boolean {
  const active = p.scan_running || p.score_pending > 0;
  return wasActive && !active && p.new_found > 0;
}

export function ScanProgressPill() {
  const { t } = useTranslation();
  const { data } = useScanProgress();
  // Local edge state: only show "Done" when a cycle we SAW run has just settled,
  // never on the initial idle mount.
  const [showDone, setShowDone] = useState(false);
  const wasActive = useRef(false);
  const doneTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!data) return;
    const active = data.scan_running || data.score_pending > 0;
    if (isScanDoneEdge(wasActive.current, data)) {
      setShowDone(true);
      if (doneTimer.current) clearTimeout(doneTimer.current);
      doneTimer.current = setTimeout(() => setShowDone(false), DONE_LINGER_MS);
    } else if (active) {
      // A fresh cycle supersedes any lingering "Done".
      setShowDone(false);
    }
    wasActive.current = active;
  }, [data]);

  // Drop the timer on unmount so a late dismiss can't fire on a gone component.
  useEffect(() => () => { if (doneTimer.current) clearTimeout(doneTimer.current); }, []);

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
      className="inline-flex items-center gap-2 rounded-7 border border-border-2 bg-surface-2 px-2.5 py-1 text-[12px] font-medium text-ink-2"
    >
      {done ? (
        // Round green check, sized to match the spinner/ring it replaces.
        <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-good text-white">
          <Icon name="check" size={12} strokeWidth={3} />
        </span>
      ) : scoring ? (
        // Determinate green ring — fills as score_done climbs toward new_found.
        <span
          className="grid h-5 w-5 shrink-0 place-items-center rounded-full"
          style={{ background: `conic-gradient(var(--good) ${pct * 360}deg, var(--surface-3) 0deg)` }}
          aria-hidden="true"
        >
          <span className="h-3 w-3 rounded-full bg-surface-2" />
        </span>
      ) : (
        // Indeterminate green spinner — discovery has no known total.
        <span
          className="inline-block h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-good/25 border-t-good"
          aria-hidden="true"
        />
      )}
      <span>
        {data.scan_running
          ? t("jobBoard.scanProgress.scanning")
          : scoring
            ? t("jobBoard.scanProgress.scoring")
            : t("jobBoard.scanProgress.done")}
      </span>
      {scoring ? (
        <span className="font-mono text-[11px] text-ink-3" data-testid="scan-progress-count">
          {t("jobBoard.scanProgress.count", { done: data.score_done, found: data.new_found })}
        </span>
      ) : null}
    </div>
  );
}
