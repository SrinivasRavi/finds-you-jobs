// The ONE "Re-score with AI?" consent dialog (maintainer 2026-07-23), shared
// by both triggers: a resume edit (Job Board) and switching Settings → Scoring
// to AI. `preview` comes from api.rescorePreview(), which counts the same
// cache-miss set a confirmed run enqueues — so the N shown always equals what
// actually runs, and jobs already AI-scored at the current resume version are
// skipped, never re-spent.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { invalidateFeed } from "../api/queries";
import type { RescorePreview } from "../api/types";
import { ConfirmDialog } from "./ConfirmDialog";

export function RescoreAiDialog({
  preview,
  reason,
  onClose,
}: {
  preview: RescorePreview;
  /** What changed: the resume content, or the scoring mode (keyword → AI). */
  reason: "resume-edit" | "mode-switch";
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const n = preview.to_score;
  const skipped =
    preview.cached > 0 ? ` ${t("shell.rescore.skipped", { count: preview.cached })}` : "";
  const body =
    reason === "resume-edit"
      ? t("shell.rescore.bodyResumeEdit", { count: n, skipped })
      : t("shell.rescore.bodyModeSwitch", { count: n, skipped });
  return (
    <ConfirmDialog
      title={t("shell.rescore.title")}
      body={body}
      confirmLabel={busy ? t("shell.rescore.busy") : t("shell.rescore.confirm", { count: n })}
      cancelLabel={t("shell.rescore.keepScores")}
      busy={busy}
      onConfirm={() => {
        setBusy(true);
        void api
          .rescoreBoard()
          .then(() => invalidateFeed(qc))
          .finally(() => {
            setBusy(false);
            onClose();
          });
      }}
      onCancel={onClose}
    />
  );
}
