// Generation dialog (US-TL-02 / US-RES-02 Re-generate) — optional per-job
// tailoring guidance textarea + a Generate CTA. Passed to the Tailorer's
// `guidance` field; not saved to the profile.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Modal } from "../shell/Modal";

export function GuidanceDialog({
  onClose,
  onGenerate,
  label = "tailored resume",
}: {
  onClose: () => void;
  onGenerate: (guidance: string) => void;
  label?: string;
}) {
  const { t } = useTranslation();
  const [guidance, setGuidance] = useState("");
  // The label prop is a stable identifier from the caller; translate it here.
  const labelText = t(
    label === "cover letter" ? "popups.guidance.labelCover" : "popups.guidance.labelResume",
  );
  return (
    <Modal title={t("popups.guidance.generate", { label: labelText })} onClose={onClose} width={480}>
      <div className="flex flex-col gap-3 p-5">
        <p className="text-[12.5px] text-ink-2">
          {t("popups.guidance.blurb", { label: labelText })}
        </p>
        <label className="text-[12px] text-ink-3" htmlFor="guidance">
          {t("popups.guidance.instructionsLabel")}
        </label>
        <textarea
          id="guidance"
          data-testid="guidance-input"
          value={guidance}
          onChange={(e) => setGuidance(e.target.value)}
          rows={4}
          placeholder={t("popups.guidance.placeholder")}
          className="resize-none rounded-md border border-border bg-surface p-3 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
        />
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
          >
            {t("popups.guidance.cancel")}
          </button>
          <button
            onClick={() => {
              onGenerate(guidance);
              onClose();
            }}
            className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
          >
            {t("popups.guidance.generate", { label: labelText })}
          </button>
        </div>
      </div>
    </Modal>
  );
}
