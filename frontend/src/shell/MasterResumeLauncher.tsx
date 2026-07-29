// The "Master Resume" header button, shared by the Job Board, Applications, and
// Networking tabs so the master resume is reachable from every surface and the
// button lands on the SAME pixel in each (it sits immediately left of the
// shared Deleted+Add cluster, whose widths are identical across tabs — see
// HeaderAddButton.tsx for the shared-right-edge alignment idea). It owns its own
// modal + AI-rescore prompt so the open/save/rescore behavior can't drift
// between tabs.

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api";
import { invalidateFeed, useProfile, useSettings, useUpdateProfile } from "../api/queries";
import type { RescorePreview } from "../api/types";
import { ResumeModal } from "../popups/ResumeModal";
import { Icon } from "./icons";
import { RescoreAiDialog } from "./RescoreAiDialog";

export function MasterResumeLauncher() {
  const { t } = useTranslation();
  const { data: profile } = useProfile();
  const { data: settings } = useSettings();
  const updateProfile = useUpdateProfile();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  // After a master-resume edit in AI mode, ask before spending tokens to
  // re-score the board (maintainer 2026-07-23). Holds the server's preview of
  // the cache misses a confirmed run would enqueue, or null when hidden.
  const [rescoreAsk, setRescoreAsk] = useState<RescorePreview | null>(null);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        data-action="open-master-resume"
        title={t("jobBoard.header.masterResumeTitle")}
        className="inline-flex h-[30px] shrink-0 items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink"
      >
        <Icon name="file" size={14} strokeWidth={2} />
        {t("jobBoard.header.masterResume")}
      </button>
      {open && profile ? (
        <ResumeModal
          kind="master"
          profile={profile}
          onClose={() => setOpen(false)}
          onSaveMaster={(md: string) => {
            // Save the resume; scores are cached per resume version. Keyword
            // mode re-scores server-side for free at save; AI mode costs
            // tokens, so preview the cache misses and ask first (declining
            // keeps the prior scores visible — the board shows the latest
            // version). An unchanged save bumps nothing and asks nothing.
            void updateProfile.mutateAsync(md).then(async () => {
              if (settings?.scoring_mode === "llm") {
                const preview = await api.rescorePreview();
                if (preview.to_score > 0) {
                  setOpen(false); // close the editor so the prompt stands alone
                  setRescoreAsk(preview);
                  return;
                }
              }
              invalidateFeed(qc); // keyword mode already re-scored / nothing to score
            });
          }}
        />
      ) : null}
      {rescoreAsk !== null ? (
        <RescoreAiDialog
          preview={rescoreAsk}
          reason="resume-edit"
          onClose={() => setRescoreAsk(null)}
        />
      ) : null}
    </>
  );
}
