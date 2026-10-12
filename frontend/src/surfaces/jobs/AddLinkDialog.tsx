// The promotion dialog (url-less-jobs.md section 9.5): 1 component behind
// every surface that needs a posting link it doesn't have. The board detail's
// Add link, the tracker detail's Add link, the Apply prompt (9.8) and the
// watch-a-company variant (D12) all open this, so the compound match check of
// section 5.4 lives in one place and can't drift between them.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAttachJobLink } from "../../api/queries";
import type { AttachLinkResult, LinkCandidate } from "../../api/types";
import { Modal } from "../../shell/Modal";

/** Which door opened it — copy and the primary button, nothing else. */
export type AddLinkMode = "link" | "apply" | "watch";

function SideBySide({ pasted, card }: { pasted: LinkCandidate; card: LinkCandidate | null }) {
  const { t } = useTranslation();
  const pct =
    pasted.similarity === null ? null : Math.round(pasted.similarity * 100);
  const rows: [string, string][] = [
    [pasted.title, card?.title ?? ""],
    [pasted.company, card?.company ?? ""],
    [pasted.location, card?.location ?? ""],
    [
      pct === null ? t("jobBoard.addLink.na") : t("jobBoard.addLink.similarity", { percent: pct }),
      t("jobBoard.addLink.na"),
    ],
  ];
  return (
    <div className="grid grid-cols-2 gap-4 text-[12.5px]" data-testid="add-link-compare">
      <div className="font-medium text-ink-2">{t("jobBoard.addLink.youPasted")}</div>
      <div className="font-medium text-ink-2">{t("jobBoard.addLink.cardSays")}</div>
      {rows.map(([left, right], i) => (
        <div key={i} className="contents">
          <div className="truncate border-t border-border pt-1.5 text-ink">{left}</div>
          <div className="truncate border-t border-border pt-1.5 text-ink">{right}</div>
        </div>
      ))}
    </div>
  );
}

export function AddLinkDialog({
  jobId,
  jobLabel,
  mode = "link",
  onClose,
  onAttached,
}: {
  jobId: string;
  jobLabel: string;
  mode?: AddLinkMode;
  onClose: () => void;
  onAttached: () => void;
}) {
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [result, setResult] = useState<AttachLinkResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const attach = useAttachJobLink();

  function run(opts: { confirm?: boolean } = {}) {
    setError(null);
    attach.mutate(
      { job_id: jobId, url: url.trim(), ...opts },
      {
        onSuccess: (r) => {
          setResult(r);
          if (r.status === "attached") onAttached();
        },
        onError: (e) => setError(e instanceof Error ? e.message : String(e)),
      },
    );
  }

  const title =
    result?.status === "mismatch"
      ? t("jobBoard.addLink.mismatchTitle")
      : result?.status === "duplicate"
        ? t("jobBoard.addLink.duplicateTitle")
        : mode === "apply"
          ? t("jobBoard.addLink.applyTitle")
          : mode === "watch"
            ? t("jobBoard.addLink.watchTitle")
            : t("jobBoard.addLink.title");

  const primary =
    mode === "apply"
      ? t("jobBoard.addLink.applyAttach")
      : mode === "watch"
        ? t("jobBoard.addLink.watchAttach")
        : t("jobBoard.addLink.attach");

  return (
    <Modal title={title} onClose={onClose} size="md">
      <div className="flex flex-col gap-3 px-5 py-4" data-testid="add-link-dialog">
        <p className="text-[12.5px] text-ink-2">{jobLabel}</p>

        {result?.status === "mismatch" && result.pasted ? (
          <>
            <SideBySide pasted={result.pasted} card={result.card} />
            <p className="text-[12.5px] leading-snug text-ink-2">
              {t("jobBoard.addLink.mismatchBody")}
            </p>
            <div className="mt-1 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setResult(null)}
                className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
              >
                {t("jobBoard.addLink.useDifferent")}
              </button>
              {/* Always available, never the default — rank-don't-gate
                  applied to a field the same way it applies to a score. */}
              <button
                type="button"
                data-testid="add-link-confirm"
                disabled={attach.isPending}
                onClick={() => run({ confirm: true })}
                className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink hover:bg-surface-3"
              >
                {t("jobBoard.addLink.attachAnyway")}
              </button>
            </div>
          </>
        ) : result?.status === "unreadable" ? (
          <>
            <p
              className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-[12.5px] leading-snug text-warn"
              data-testid="add-link-unreadable"
            >
              ⚠ {t("jobBoard.addLink.unreadable")}
            </p>
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
                data-testid="add-link-confirm"
                disabled={attach.isPending}
                onClick={() => run({ confirm: true })}
                className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
              >
                {t("jobBoard.addLink.attachAnyway")}
              </button>
            </div>
          </>
        ) : result?.status === "duplicate" ? (
          <>
            {/* 2 jobs want 1 link. The user deletes the one they don't need;
                the app never merges them (url-less-jobs.md section 5.5). */}
            <p className="text-[12.5px] leading-snug text-ink-2" data-testid="add-link-duplicate">
              {t("jobBoard.addLink.duplicateBody")}
            </p>
            {result.pasted ? <SideBySide pasted={result.pasted} card={null} /> : null}
            <div className="mt-1 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
              >
                {t("jobBoard.addLink.cancel")}
              </button>
            </div>
          </>
        ) : (
          <>
            <input
              type="url"
              autoFocus
              data-testid="add-link-input"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://"
              className="rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
            />
            <p className="text-[11.5px] leading-snug text-ink-4">
              ↳ {mode === "watch" ? t("jobBoard.addLink.watchHint") : t("jobBoard.addLink.hint")}
            </p>
            {mode === "apply" ? (
              <p className="text-[12.5px] leading-snug text-ink-2">
                {t("jobBoard.addLink.applyBody")}
              </p>
            ) : null}
            {error !== null ? (
              <p
                className="rounded-md border border-bad/40 bg-bad-wash px-3 py-2 text-[12px] text-bad"
                data-testid="add-link-error"
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
                data-testid="add-link-attach"
                disabled={!url.trim() || attach.isPending}
                onClick={() => run()}
                className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
              >
                {attach.isPending ? t("jobBoard.addLink.checking") : primary}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
