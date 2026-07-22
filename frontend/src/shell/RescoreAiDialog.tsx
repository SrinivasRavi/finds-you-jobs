// The ONE "Re-score with AI?" consent dialog (maintainer 2026-07-23), shared
// by both triggers: a resume edit (Job Board) and switching Settings → Scoring
// to AI. `preview` comes from api.rescorePreview(), which counts the same
// cache-miss set a confirmed run enqueues — so the N shown always equals what
// actually runs, and jobs already AI-scored at the current resume version are
// skipped, never re-spent.
import { useState } from "react";
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
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const n = preview.to_score;
  const skipped =
    preview.cached > 0
      ? ` (${preview.cached} already ${preview.cached === 1 ? "has" : "have"} an AI score for this resume — skipped.)`
      : "";
  const body =
    reason === "resume-edit"
      ? `Your resume changed. Re-score ${n} job${n === 1 ? "" : "s"} against it with AI?${skipped} This uses your LLM key — one call per job. Or keep the current scores; you can re-score anytime by editing your resume again.`
      : `Score the ${n} job${n === 1 ? "" : "s"} on your board that ${n === 1 ? "has" : "have"} no AI score yet?${skipped} This uses your LLM key — one call per job. New jobs from future scans are AI-scored automatically either way.`;
  return (
    <ConfirmDialog
      title="Re-score jobs with AI?"
      body={body}
      confirmLabel={busy ? "Re-scoring…" : `Re-score ${n} job${n === 1 ? "" : "s"}`}
      cancelLabel="Keep current scores"
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
