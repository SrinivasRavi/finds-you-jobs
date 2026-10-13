// The "Master Resume" header button, shared by the 3 tabs so it lands on the
// same pixel in each, left of the Deleted and Add cluster. It owns its modal, so
// open and save can't drift between tabs.

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { invalidateFeed, useProfile, useUpdateProfile } from "../api/queries";
import { ResumeModal } from "../popups/ResumeModal";
import { Icon } from "./icons";

export function MasterResumeLauncher() {
  const { t } = useTranslation();
  const { data: profile } = useProfile();
  const updateProfile = useUpdateProfile();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

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
            // Save the resume. Keyword mode re-scores the board server-side for
            // free at save; AI mode never re-spends on a job that already has a
            // score, so the prior LLM scores stay visible (S-C24, maintainer
            // 2026-08-28). An unchanged save bumps nothing.
            void updateProfile.mutateAsync(md).then(() => invalidateFeed(qc));
          }}
        />
      ) : null}
    </>
  );
}
