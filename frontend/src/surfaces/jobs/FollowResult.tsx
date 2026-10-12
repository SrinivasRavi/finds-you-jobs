// What following a company's board found (docs/internal/watch-company.md
// section 3). A follow is never refused: a board that reads badly is followed
// with a warning, and the link is always shown so the user can check it.

import { useTranslation } from "react-i18next";

import type { WatchCompanyResult } from "../../api/types";

/** 2 or more openings read cleanly: nothing to warn about. */
export const followLooksRight = (r: WatchCompanyResult): boolean =>
  r.openings !== null && r.openings >= 2 && !r.read_error;

export function FollowResult({
  result,
  onChange,
}: {
  result: WatchCompanyResult;
  onChange?: () => void;
}) {
  const { t } = useTranslation();
  const n = result.openings;
  const fine = followLooksRight(result);
  const message = result.read_error
    ? t("jobBoard.follow.readFailed", { reason: result.read_error })
    : n === 0
      ? t("jobBoard.follow.none")
      : n === 1
        ? t("jobBoard.follow.single")
        : t("jobBoard.follow.following", { count: n ?? 0, company: result.company || result.source_url });
  return (
    <div
      data-testid="follow-result"
      data-ok={fine}
      className={`space-y-1 rounded-md border px-3 py-2 text-[12px] ${
        fine ? "border-good/40 bg-good-wash text-ink" : "border-warn/40 bg-warn-wash text-ink"
      }`}
    >
      <p>{fine ? message : `⚠ ${message}`}</p>
      {fine && result.sample_titles.length ? (
        <p className="text-ink-3">{result.sample_titles.join(" · ")}</p>
      ) : null}
      <p className="flex flex-wrap items-center gap-x-2 text-ink-3">
        {t("jobBoard.follow.link")}{" "}
        <a
          href={result.source_url}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="follow-link"
          className="break-all text-accent-ink underline"
        >
          {result.source_url}
        </a>
        {onChange ? (
          <button
            type="button"
            onClick={onChange}
            data-testid="follow-change"
            className="text-accent-ink underline"
          >
            {t("jobBoard.follow.change")}
          </button>
        ) : null}
      </p>
    </div>
  );
}
