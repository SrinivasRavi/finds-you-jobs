// Try a prompt before committing it (url-less-jobs.md section 11, S-C70). Save
// used to be the only way to run one, breaking the working default for the whole
// loop. The draft and its test runs live in this modal and 1 button commits; a
// test runs the draft, everything else the saved prompt, and Reset on the
// Settings row restores the shipped one.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  RunStoppedError,
  useBoard,
  useCancelOperation,
  useContacts,
  usePromptTest,
  useSetPrompt,
  useSettings,
} from "../../api/queries";
import type { Job, PromptSetting, PromptTestResult } from "../../api/types";
import { Markdown } from "../../shell/Markdown";
import { Modal } from "../../shell/Modal";
import { scoreText } from "../../shell/scoreText";

/** What each kind needs before Run can do anything (url-less-jobs.md 11.3). */
const NEEDS_JOB = new Set(["score", "tailor", "cover"]);
const NEEDS_CONTACT = new Set(["networker_draft"]);

/** The run button, per kind. A button is read to find out what is about to
 *  happen, and "Run this draft once" named our mechanism instead of the work
 *  (maintainer, 2026-10-08). An unregistered kind gets the generic label. */
export const RUN_LABEL_KEY: Record<string, string> = {
  score: "settingsPage.prompts.testRunScore",
  tailor: "settingsPage.prompts.testRunTailor",
  cover: "settingsPage.prompts.testRunCover",
  extract: "settingsPage.prompts.testRunExtract",
  networker_draft: "settingsPage.prompts.testRunNetworkerDraft",
};

type JobSource = "board" | "paste";

/** Both columns open with the same 2-line block, so their bodies start on the
 *  same baseline whatever each one holds. */
function ColumnHead({ title, hint }: { title: string; hint: string }) {
  return (
    <header className="min-h-[38px]">
      <h3 className="m-0 text-[13px] font-semibold text-ink">{title}</h3>
      <p className="mt-0.5 text-[11.5px] text-ink-3">{hint}</p>
    </header>
  );
}

function Output({ result }: { result: PromptTestResult }) {
  const { t } = useTranslation();
  if (result.state === "failed") {
    return (
      <div data-testid="prompt-test-error" className="text-[12px] text-bad">
        {result.error || t("settingsPage.prompts.testFailed")}
      </div>
    );
  }
  return (
    <div data-testid="prompt-test-output" className="space-y-2">
      {result.usd != null ? (
        <p className="text-[11px] text-ink-3">
          {t("settingsPage.prompts.testCost", { usd: result.usd.toFixed(4) })}
        </p>
      ) : null}
      {result.score != null ? (
        <div>
          <div className="text-[15px] font-semibold text-ink">{result.score}</div>
          <ul className="mt-1 space-y-1 text-[12px] text-ink-2">
            {(result.reasons ?? []).map((r, i) => (
              <li key={i}>{scoreText(r, t)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {result.output_md ? <Markdown md={result.output_md} className="text-[12.5px]" /> : null}
      {result.message ? (
        <p className="whitespace-pre-wrap text-[12.5px] text-ink-2">{result.message}</p>
      ) : null}
      {result.profile ? (
        <ul className="space-y-1 text-[12px] text-ink-2">
          {Object.entries(result.profile)
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <li key={k}>
                <span className="text-ink-3">{k}: </span>
                {String(v)}
              </li>
            ))}
        </ul>
      ) : null}
      {result.notes && result.notes.length > 0 ? (
        <ul className="mt-2 space-y-1 border-t border-border pt-2 text-[11.5px] text-ink-3">
          {result.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function PromptTuningModal({
  prompt,
  name,
  onClose,
}: {
  prompt: PromptSetting;
  /** What the tab behind this window calls it, so the 2 can never disagree. */
  name: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const saved = prompt.override_md ?? prompt.default_md;
  const [draft, setDraft] = useState(saved);
  const [source, setSource] = useState<JobSource>("board");
  const [jobId, setJobId] = useState("");
  const [pasted, setPasted] = useState("");
  const [contactId, setContactId] = useState("");
  const [result, setResult] = useState<PromptTestResult | null>(null);

  const needsJob = NEEDS_JOB.has(prompt.kind);
  const needsContact = NEEDS_CONTACT.has(prompt.kind);
  const board = useBoard();
  const contacts = useContacts();
  const test = usePromptTest();
  const cancel = useCancelOperation();
  // The running test's operation, so Stop can cancel it (audit finding 4).
  const [runningOp, setRunningOp] = useState<string | null>(null);
  const setPrompt = useSetPrompt();
  const settings = useSettings();
  // By OPERATION kind, not prompt kind: `networker_draft` runs as `draft`,
  // and looking it up by its own name found nothing, so the referral test
  // run never said what it would run on.
  const route = settings.data?.routing.find((r) => r.kind === prompt.op_kind);

  const jobs = board.data?.pages.flatMap((p) => p.jobs) ?? [];
  const dirty = draft !== saved;
  const ready =
    draft.trim().length > 0 &&
    (!needsJob || (source === "board" ? !!jobId : pasted.trim().length > 0)) &&
    (!needsContact || !!contactId);

  // Where the job is and what it already scored, because a picker that says
  // only the title and company is a picker whose first row always wins. The
  // board hands over 1 score per job, already resolved model-over-keyword, so
  // there is no method to choose between here (maintainer, 2026-10-08).
  function jobLabel(job: Job): string {
    const parts = [job.title, job.company, job.location].filter(Boolean);
    const n = job.score?.score_0_100;
    if (n != null) parts.push(t("settingsPage.prompts.jobOptionScore", { n }));
    return parts.join(" · ");
  }

  function run() {
    setResult(null);
    test.mutate(
      {
        kind: prompt.kind,
        onStarted: setRunningOp,
        input: {
          markdown: draft,
          ...(needsJob && source === "board" ? { job_id: jobId } : {}),
          ...(needsJob && source === "paste" ? { job_description: pasted } : {}),
          ...(needsContact ? { contact_id: contactId } : {}),
        },
      },
      {
        onSuccess: setResult,
        onError: (e) =>
          setResult({
            state: "failed",
            error:
              e instanceof RunStoppedError
                ? t(e.reason === "timeout" ? "run.timedOut" : "run.stopped")
                : String(e),
          }),
        onSettled: () => setRunningOp(null),
      },
    );
  }

  return (
    <Modal
      title={t("settingsPage.prompts.tuneTitle", { name })}
      onClose={onClose}
      size="xl"
      footer={
        <div className="flex items-center gap-3">
          <p className="flex-1 text-[11.5px] text-ink-3">
            {t("settingsPage.prompts.commitHint")}
          </p>
          <button
            data-testid="prompt-tune-cancel"
            onClick={onClose}
            className="rounded-md border border-border-2 px-3 py-1.5 text-[12px] text-ink-2"
          >
            {t("settingsPage.prompts.close")}
          </button>
          <button
            data-testid="prompt-tune-commit"
            disabled={!dirty || setPrompt.isPending}
            onClick={() =>
              setPrompt.mutate({ kind: prompt.kind, markdown: draft }, { onSuccess: onClose })
            }
            className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-40"
          >
            {t("settingsPage.prompts.commit")}
          </button>
        </div>
      }
    >
      <div className="grid grid-cols-[1.05fr_1fr] gap-5 p-5">
        <section className="flex min-w-0 flex-col gap-2">
          <ColumnHead
            title={t("settingsPage.prompts.draftTitle")}
            hint={t("settingsPage.prompts.draftLabel")}
          />
          <textarea
            data-testid="prompt-tune-draft"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="h-[58vh] w-full resize-none rounded-md border border-border-2 bg-surface-2 p-3 font-mono text-[12px] leading-relaxed text-ink"
          />
          <div className="text-[11px] text-ink-4">
            {t("settingsPage.prompts.chars", { n: draft.length })}
          </div>
        </section>

        <section className="flex min-w-0 flex-col gap-2">
          <ColumnHead
            title={t("settingsPage.prompts.tryTitle")}
            hint={
              needsJob
                ? t("settingsPage.prompts.tryHintJob")
                : needsContact
                  ? t("settingsPage.prompts.tryHintContact")
                  : t("settingsPage.prompts.tryHintNone")
            }
          />
          {needsJob ? (
            <>
              <div className="flex gap-1.5">
                {(["board", "paste"] as JobSource[]).map((s) => (
                  <button
                    key={s}
                    data-testid={`prompt-test-source-${s}`}
                    aria-pressed={source === s}
                    onClick={() => setSource(s)}
                    className={
                      "rounded-7 border px-2.5 py-1 text-[12px] " +
                      (source === s
                        ? "border-accent bg-accent text-white"
                        : "border-border-2 bg-surface text-ink-2")
                    }
                  >
                    {s === "board"
                      ? t("settingsPage.prompts.sourceBoard")
                      : t("settingsPage.prompts.sourcePaste")}
                  </button>
                ))}
              </div>
              {source === "board" ? (
                <select
                  data-testid="prompt-test-job"
                  value={jobId}
                  onChange={(e) => setJobId(e.target.value)}
                  className="h-[32px] rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink"
                >
                  <option value="">{t("settingsPage.prompts.pickJob")}</option>
                  {jobs.map((j) => (
                    <option key={j.id} value={j.id}>
                      {jobLabel(j)}
                    </option>
                  ))}
                </select>
              ) : (
                <textarea
                  data-testid="prompt-test-paste"
                  value={pasted}
                  onChange={(e) => setPasted(e.target.value)}
                  placeholder={t("settingsPage.prompts.pastePlaceholder")}
                  className="h-24 w-full resize-none rounded-md border border-border-2 bg-surface p-2 text-[12px] text-ink"
                />
              )}
            </>
          ) : null}

          {needsContact ? (
            <select
              data-testid="prompt-test-contact"
              value={contactId}
              onChange={(e) => setContactId(e.target.value)}
              className="h-[32px] rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink"
            >
              <option value="">{t("settingsPage.prompts.pickContact")}</option>
              {(contacts.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} · {c.current_company}
                </option>
              ))}
            </select>
          ) : null}

          {/* `disabled:opacity-40` rather than a disabled colour: index.css
              transitions any element whose class string merely CONTAINS
              "bg-surface" or "text-ink" over 240ms, so a colour variant would
              fade in over a quarter-second every time this enables. */}
          <button
            data-testid="prompt-tune-run"
            disabled={!ready || test.isPending}
            onClick={run}
            className="rounded-md bg-accent px-3 py-2 text-[12.5px] font-medium text-white disabled:opacity-40"
          >
            {test.isPending
              ? t("settingsPage.prompts.testRunning")
              : t(RUN_LABEL_KEY[prompt.kind] ?? "settingsPage.prompts.testRun")}
          </button>
          {test.isPending && runningOp ? (
            <button
              type="button"
              data-testid="prompt-tune-stop"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(runningOp)}
              className="rounded-md border border-border-2 px-3 py-2 text-[12.5px] text-ink-2 disabled:opacity-40"
            >
              {t("run.stop")}
            </button>
          ) : null}
          {/* One line, not two: what it will run on, and what that costs the
              user. The dollar figure is only knowable afterwards, and the
              result panel reports it (url-less-jobs.md section 11.4). */}
          <p className="text-[11px] leading-relaxed text-ink-3" data-testid="prompt-tune-engine">
            {route
              ? t("settingsPage.prompts.testOn", {
                  engine: route.engine,
                  model: route.model || t("settingsPage.prompts.providerDefault"),
                }) + " "
              : ""}
            {t("settingsPage.prompts.testHint")}
          </p>

          <div className="min-h-[34vh] flex-1 overflow-y-auto rounded-md border border-border bg-surface-2 p-3">
            {result ? (
              <Output result={result} />
            ) : (
              <p className="text-[12px] text-ink-3">
                {needsJob
                  ? t("settingsPage.prompts.testEmptyJob")
                  : needsContact
                    ? t("settingsPage.prompts.testEmptyContact")
                    : t("settingsPage.prompts.testEmpty")}
              </p>
            )}
          </div>
        </section>
      </div>
    </Modal>
  );
}
