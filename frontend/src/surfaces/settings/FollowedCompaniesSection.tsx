// The boards the user follows (docs/internal/watch-company.md section 3). It
// lives with the other sources because a followed board IS a row in the
// sources list. Each row shows the link it follows, so the user can check it.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useUnwatchCompany, useWatchCompany, useWatchlist } from "../../api/queries";
import type { WatchCompanyResult, WatchlistEntry } from "../../api/types";
import { FollowResult } from "../jobs/FollowResult";

const INPUT =
  "h-[30px] flex-1 rounded-7 border border-border-2 bg-surface px-2 text-[12.5px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none";
const BUTTON =
  "rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50";

export function FollowedCompaniesSection() {
  const { t } = useTranslation();
  const { data: entries } = useWatchlist();
  const watch = useWatchCompany();
  const unwatch = useUnwatchCompany();
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<WatchCompanyResult | null>(null);
  // The row whose link is being changed: the old link, and the new one typed.
  const [changing, setChanging] = useState<{ from: WatchlistEntry; to: string } | null>(null);
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  async function follow(next: string, company?: string): Promise<WatchCompanyResult | null> {
    setError("");
    try {
      const r = await watch.mutateAsync({ url: next.trim(), company });
      setResult(r);
      return r;
    } catch (e) {
      fail(e);
      return null;
    }
  }

  /** Change link: follow the new one first, so a bad paste never loses the old. */
  async function change() {
    if (!changing?.to.trim()) return;
    const r = await follow(changing.to, changing.from.company);
    if (r) {
      if (r.source_url !== changing.from.url) {
        await unwatch.mutateAsync(changing.from.url).catch(fail);
      }
      setChanging(null);
    }
  }

  return (
    <div className="space-y-2" data-testid="follow-section">
      <p className="text-[12px] text-ink-3">{t("jobBoard.prefs.tracked.hint")}</p>
      <ul className="space-y-1">
        {(entries ?? []).map((e) => (
          <li
            key={e.url}
            data-testid="follow-row"
            className="space-y-1.5 rounded-7 border border-border-2 bg-surface px-2.5 py-1.5"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-medium text-ink">
                  {e.company || e.url}
                  <span
                    className={`ml-2 font-normal ${e.read_error ? "text-warn" : "text-ink-3"}`}
                    title={e.read_error || undefined}
                  >
                    {e.read_error
                      ? t("jobBoard.follow.couldNotRead")
                      : e.openings === null
                        ? t("jobBoard.follow.notChecked")
                        : t("jobBoard.follow.openings", { count: e.openings })}
                  </span>
                </div>
                <a
                  href={e.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block truncate text-[11px] text-accent-ink underline"
                >
                  {e.url}
                </a>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  data-testid="follow-row-change"
                  onClick={() => setChanging({ from: e, to: e.url })}
                  className="text-[11.5px] text-accent-ink underline"
                >
                  {t("jobBoard.follow.change")}
                </button>
                <button
                  type="button"
                  data-testid="follow-remove"
                  aria-label={t("jobBoard.prefs.tracked.stopTrackingAria")}
                  onClick={() => {
                    setError("");
                    unwatch.mutate(e.url, { onError: fail });
                  }}
                  className="text-ink-3 hover:text-bad"
                >
                  ×
                </button>
              </div>
            </div>
            {changing?.from.url === e.url ? (
              <div className="flex items-center gap-2">
                <input
                  autoFocus
                  value={changing.to}
                  data-testid="follow-change-input"
                  onChange={(ev) => setChanging({ from: e, to: ev.target.value })}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter") void change();
                    if (ev.key === "Escape") setChanging(null);
                  }}
                  className={INPUT}
                />
                <button
                  type="button"
                  data-testid="follow-change-save"
                  disabled={watch.isPending}
                  onClick={() => void change()}
                  className={BUTTON}
                >
                  {t("jobBoard.follow.useLink")}
                </button>
              </div>
            ) : null}
          </li>
        ))}
        {(entries ?? []).length === 0 ? (
          <li className="rounded-7 border border-dashed border-border-2 px-2.5 py-1.5 text-[12px] text-ink-3">
            {t("jobBoard.prefs.tracked.empty")}
          </li>
        ) : null}
      </ul>
      <div className="flex items-center gap-2">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && url.trim()) {
              void follow(url).then((r) => r && setUrl(""));
            }
          }}
          placeholder="https://boards.greenhouse.io/…"
          data-testid="follow-url"
          className={INPUT}
        />
        <button
          type="button"
          disabled={watch.isPending || !url.trim()}
          data-testid="follow-add"
          onClick={() => void follow(url).then((r) => r && setUrl(""))}
          className={BUTTON}
        >
          {watch.isPending ? t("jobBoard.prefs.tracked.adding") : t("jobBoard.prefs.tracked.track")}
        </button>
      </div>
      {result ? (
        <FollowResult
          result={result}
          onChange={() => {
            const entry = entries?.find((x) => x.url === result.source_url);
            if (entry) setChanging({ from: entry, to: entry.url });
          }}
        />
      ) : null}
      {error ? (
        <p className="text-[11.5px] text-bad" data-testid="follow-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
