// "Show more jobs from this company" on a job the app can't trace to a board
// (docs/internal/watch-company.md). It asks for the careers page and follows
// it; it never touches the job's own link, which is what Add link is for.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useWatchCompany } from "../../api/queries";
import type { WatchCompanyResult } from "../../api/types";
import { Modal } from "../../shell/Modal";
import { FollowResult } from "./FollowResult";

export function FollowCompanyDialog({
  company,
  onClose,
}: {
  company: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const watch = useWatchCompany();
  const [url, setUrl] = useState("");
  const [result, setResult] = useState<WatchCompanyResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  function follow() {
    setError(null);
    watch.mutate(
      { url: url.trim(), company },
      {
        onSuccess: (r) => setResult(r),
        onError: (e) => setError(e instanceof Error ? e.message : String(e)),
      },
    );
  }

  return (
    <Modal title={t("jobBoard.addLink.watchTitle")} onClose={onClose} size="md">
      <div className="flex flex-col gap-3 px-5 py-4" data-testid="follow-dialog">
        <p className="text-[12.5px] text-ink-2">{company}</p>
        {result ? (
          <>
            <FollowResult result={result} onChange={() => setResult(null)} />
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onClose}
                data-testid="follow-done"
                className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
              >
                {t("jobBoard.follow.done")}
              </button>
            </div>
          </>
        ) : (
          <>
            <input
              type="url"
              autoFocus
              data-testid="follow-input"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && url.trim()) follow();
              }}
              placeholder="https://"
              className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
            />
            <p className="text-[11.5px] leading-snug text-ink-4">↳ {t("jobBoard.addLink.watchHint")}</p>
            {error !== null ? (
              <p
                className="rounded-md border border-bad/40 bg-bad-wash px-3 py-2 text-[12px] text-bad"
                data-testid="follow-error"
              >
                {error}
              </p>
            ) : null}
            <div className="mt-1 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
              >
                {t("jobBoard.addLink.cancel")}
              </button>
              <button
                type="button"
                data-testid="follow-submit"
                disabled={!url.trim() || watch.isPending}
                onClick={follow}
                className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
              >
                {watch.isPending ? t("jobBoard.addLink.checking") : t("jobBoard.addLink.watchAttach")}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
