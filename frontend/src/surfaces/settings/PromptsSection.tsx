// Editable prompts (FR-SET-11): 1 prompt, rendered by the Settings category that
// owns its work (url-less-jobs.md section 11.5), e.g. the rubric under Scoring.
// The engine and model control is `ModelPicker`, keyed on the operation kind,
// because 2 routed kinds have no prompt.

import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

import { usePrompts, useResetPrompt } from "../../api/queries";
import type { PromptSetting } from "../../api/types";
import { PromptTuningModal } from "./PromptTuningModal";

/** What each prompt governs and what it doesn't. `score` is the AI rubric only:
 *  keyword scoring is rules, Jev scoring carries its own bands, Laya scoring has no
 *  prompt, so it changes 1 of the 4 methods. */
export const SCOPE_KEY: Record<string, string> = {
  score: "settingsPage.prompts.scopeScore",
  tailor: "settingsPage.prompts.scopeTailor",
  cover: "settingsPage.prompts.scopeCover",
  extract: "settingsPage.prompts.scopeExtract",
  networker_draft: "settingsPage.prompts.scopeNetworkerDraft",
};

/** The name of a prompt, wherever it is shown: the card, the modal title, the
 *  pointer from the Scoring pane. `score` reads as the scoring method it
 *  belongs to, so the Scoring pane and this one can never call it 2 different
 *  things. An unknown kind falls back to whatever the sidecar called it. */
export const TITLE_KEY: Record<string, string> = {
  score: "scoreMethod.llm",
  tailor: "settingsPage.prompts.titleTailor",
  cover: "settingsPage.prompts.titleCover",
  extract: "settingsPage.prompts.titleExtract",
  networker_draft: "settingsPage.prompts.titleNetworkerDraft",
};

/** One prompt's read-only body, Reset, and the door to the test window.
 *  Fetches the prompt list itself so a section can drop in a single kind
 *  without threading the query through. Renders nothing until it loads. */
export const PromptCard = memo(function PromptCard({ kind }: { kind: string }) {
  const { data: prompts } = usePrompts();
  const prompt = (prompts ?? []).find((p) => p.kind === kind);
  if (!prompt) return null;
  return <PromptBody prompt={prompt} />;
});

function PromptBody({ prompt }: { prompt: PromptSetting }) {
  const { t } = useTranslation();
  const [tuning, setTuning] = useState(false);
  const resetPrompt = useResetPrompt();

  const edited = prompt.override_md != null;
  const text = prompt.override_md ?? prompt.default_md;

  function reset() {
    if (!window.confirm(t("settingsPage.prompts.resetConfirm"))) return;
    resetPrompt.mutate(prompt.kind);
  }

  const scopeKey = SCOPE_KEY[prompt.kind];
  return (
    <div className="flex flex-col gap-3">
      {scopeKey ? (
        <p
          data-testid={`prompt-scope-${prompt.kind}`}
          className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[11.5px] text-ink-2"
        >
          {t(scopeKey)}
        </p>
      ) : null}
      {/* Read-only here, editable in the modal. That IS the rule about which
          prompt runs where: a test run uses the draft, everything else uses
          this, and the boundary is a window rather than a sentence of copy
          (url-less-jobs.md section 11.2). */}
      <textarea
        value={text}
        readOnly
        spellCheck={false}
        data-testid={`prompt-textarea-${prompt.kind}`}
        className="h-[46vh] min-h-[280px] w-full resize-y rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-[12px] leading-relaxed text-ink-2"
      />
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-ink-4" data-testid={`prompt-chars-${prompt.kind}`}>
          {t("settingsPage.prompts.charCount", { n: text.length })}
          {edited
            ? t("settingsPage.prompts.overrideActive")
            : t("settingsPage.prompts.shippedDefault")}
        </span>
        <div className="flex items-center gap-2">
          {/* Reset stays OUT here, so the way back to what we ship is always 1
              click away and never behind the editor (maintainer, 2026-10-06). */}
          <button
            type="button"
            onClick={reset}
            disabled={!edited}
            data-testid={`prompt-reset-${prompt.kind}`}
            className="rounded-md border border-border px-2.5 py-1 text-[12px] text-ink-2 disabled:opacity-40"
          >
            {t("settingsPage.prompts.resetToDefault")}
          </button>
          {/* Outlined, not filled: the loudest control on this row would
              otherwise be the one that changes nothing on its own
              (maintainer, 2026-10-08). */}
          <button
            type="button"
            onClick={() => setTuning(true)}
            data-testid={`prompt-edit-${prompt.kind}`}
            className="rounded-md border border-accent px-2.5 py-1 text-[12px] font-medium text-accent-ink hover:bg-accent-wash"
          >
            {t("settingsPage.prompts.openEditor")}
          </button>
        </div>
      </div>
      {tuning ? (
        <PromptTuningModal
          prompt={prompt}
          name={TITLE_KEY[prompt.kind] ? t(TITLE_KEY[prompt.kind]) : prompt.title}
          onClose={() => setTuning(false)}
        />
      ) : null}
    </div>
  );
}
