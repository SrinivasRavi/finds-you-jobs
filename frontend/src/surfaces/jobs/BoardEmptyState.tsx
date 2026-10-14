import { useTranslation } from "react-i18next";
import { type BoardPage } from "../../api/types";
import { relativeTime, shortAgo } from "../jobFormat";

// Explained empty-feed state (US-JB-09 / FR-JB-10): scrape-running (spinner +
// live "N found so far", no time estimate), scrape-empty, or the last-scrape
// error with a timestamp — never a silent blank board.
export function BoardEmptyState({
  meta,
  loading,
  filteredOut,
  onAddJob,
}: {
  meta: BoardPage | undefined;
  loading: boolean;
  filteredOut: boolean;
  onAddJob: () => void;
}) {
  const { t } = useTranslation();
  const wrap = "grid h-full place-items-center px-6 text-center text-[12.5px] text-ink-3";
  // Filters or a search hid every row — distinct from a genuinely empty scrape.
  if (filteredOut) {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="filtered">
        {t("jobBoard.empty.filtered")}
      </div>
    );
  }
  const status = loading ? "running" : (meta?.scan_status ?? "empty");
  if (status === "running") {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-running">
        <div className="flex flex-col items-center gap-2">
          <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
          <div className="font-medium text-ink-2">{t("jobBoard.empty.scanning")}</div>
          <div data-testid="scrape-found-count">
            {t("jobBoard.empty.foundSoFar", { count: meta?.total ?? 0 })}
          </div>
        </div>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-error">
        <div className="flex flex-col items-center gap-1">
          <div className="font-medium text-bad">{t("jobBoard.empty.scrapeFailed")}</div>
          <div className="max-w-[360px] text-ink-3">
            {meta?.scan_error || t("jobBoard.empty.unknownError")}
          </div>
          <div className="text-ink-4">
            {t("jobBoard.empty.lastAttempt", { ago: relativeTime(shortAgo(meta?.last_scan_at)) })}
          </div>
        </div>
      </div>
    );
  }
  // scrape-empty (genuinely zero rows) — offer the escape hatches.
  return (
    <div className={wrap} data-testid="board-empty" data-empty-reason="scrape-empty">
      <div className="flex flex-col items-center gap-1">
        <div className="font-medium text-ink-2">{t("jobBoard.empty.noMatches")}</div>
        <div className="text-ink-3">
          {t("jobBoard.empty.widenPrefs")}
        </div>
        <div className="text-ink-4">
          {t("jobBoard.empty.lastRefresh", { ago: relativeTime(shortAgo(meta?.last_scan_at)) })}
        </div>
        {/* D13: 1 button, reusing the same modal. A user whose first scan
            found nothing is the user with a JD sitting in their inbox. */}
        <button
          type="button"
          data-testid="empty-add-job"
          onClick={onAddJob}
          className="mt-3 rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
        >
          {t("jobBoard.empty.addJob")}
        </button>
        <div className="text-ink-4">{t("jobBoard.empty.gotOne")}</div>
      </div>
    </div>
  );
}
