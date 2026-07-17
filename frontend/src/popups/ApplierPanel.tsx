// Applier companion panel (applier.md §8.2-§8.4) — the surface the app shows
// *during and after* an Apply Run. The real work happens in a separate headed
// Chromium tab; this panel narrates it, holds the evidence, and carries the P1
// handoff. Modeled on ReferralsModal's Modal + eventBus subscription pattern.
//
// It must work whether it was open the whole time OR reopened after the fact
// (§9.2): the live event feed is SEEDED from the run snapshot's fields/blockers,
// then APPENDED to from `apply` SSE events for this run_id. Closing the panel
// never cancels the run (§8.2) — it just closes.

import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import { eventBus, type SSEEvent } from "../api/events";
import { useApplyRun, useAttestApply, useCancelApply, useStartApply } from "../api/queries";
import type { ApplyRun, ApplyRunStatus } from "../api/types";
import { Modal } from "../shell/Modal";

type Tone = "info" | "ok" | "warn" | "bad";

type FeedItem = { id: string; text: string; tone: Tone };

const PILL_CLS: Record<Tone, string> = {
  info: "border-border-2 bg-surface-2 text-ink-2",
  ok: "border-good bg-good-wash text-good",
  warn: "border-warn bg-warn-wash text-warn",
  bad: "border-bad bg-bad-wash text-bad",
};
const DOT_CLS: Record<Tone, string> = {
  info: "bg-accent",
  ok: "bg-good",
  warn: "bg-warn",
  bad: "bg-bad",
};

/** The §8.2 high-level phase pill, mapped from status (+ the free-text phase
 *  while running). Spinner shows only while the run is genuinely live. */
function phaseInfo(status: ApplyRunStatus, phase: string): { label: string; tone: Tone; live: boolean } {
  switch (status) {
    case "queued":
      return { label: "Queued", tone: "info", live: true };
    case "waiting_for_packet":
      return { label: "Waiting for résumé", tone: "info", live: true };
    case "ready_for_human":
      return { label: "Ready for review", tone: "warn", live: false };
    case "blocked":
      return { label: "Blocked", tone: "bad", live: false };
    case "timed_out":
      return { label: "Timed out", tone: "bad", live: false };
    case "interrupted":
      return { label: "Interrupted", tone: "bad", live: false };
    case "failed":
      return { label: "Failed", tone: "bad", live: false };
    case "submitted":
      return { label: "Submitted", tone: "ok", live: false };
    case "running": {
      const p = phase.toLowerCase();
      if (p.includes("open")) return { label: "Opening job", tone: "info", live: true };
      if (p.includes("find") || p.includes("form")) return { label: "Finding form", tone: "info", live: true };
      if (p.includes("fill")) return { label: "Filling", tone: "info", live: true };
      if (p.includes("verif")) return { label: "Verifying", tone: "info", live: true };
      return { label: "Working", tone: "info", live: true };
    }
    default:
      return { label: "Completed", tone: "ok", live: false };
  }
}

const NON_SUCCESS_TERMINALS: ApplyRunStatus[] = ["blocked", "timed_out", "interrupted", "failed"];

/** Seed the feed from the run snapshot — every prior field outcome + blocker,
 *  so a reopened panel is never blank (§9.2). */
function seedFeed(run: ApplyRun): FeedItem[] {
  const items: FeedItem[] = [];
  run.fields.forEach((f, i) => {
    items.push({
      id: `seed-field-${i}`,
      text: `${f.label || "Field"} — ${f.action}${f.note ? ` (${f.note})` : ""}`,
      tone: f.ok ? "ok" : "warn",
    });
  });
  run.blockers.forEach((b, i) => {
    items.push({
      id: `seed-blocker-${i}`,
      text: `Blocker — ${b.kind}${b.field_label ? ` [${b.field_label}]` : ""}${b.detail ? `: ${b.detail}` : ""}`,
      tone: "bad",
    });
  });
  if (run.summary) items.push({ id: "seed-summary", text: run.summary, tone: "info" });
  return items;
}

type ApplyEventPayload = {
  run_id?: string;
  event?: string;
  phase?: string;
  label?: string;
  tool?: string;
  note?: string;
  kind?: string;
  detail?: string;
  url?: string;
  title?: string;
  field_label?: string;
  action?: string;
};

/** Map one redacted `apply` SSE event to a feed line. Returns null for events
 *  that carry no narration (screenshot_ready drives the image, not the feed). */
function formatApplyEvent(p: ApplyEventPayload): FeedItem | null {
  const id = `ev-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const who = p.label || p.tool || "Action";
  switch (p.event) {
    case "apply.phase_changed":
      return { id, text: `Phase — ${p.phase ?? ""}`, tone: "info" };
    case "apply.observed":
      return { id, text: `Observing${p.title ? ` — ${p.title}` : p.url ? ` — ${p.url}` : ""}`, tone: "info" };
    case "apply.action_started":
      return { id, text: `${who} — ${p.tool ?? "started"}…`, tone: "info" };
    case "apply.action_verified":
      return { id, text: `${who} — verified`, tone: "ok" };
    case "apply.action_failed":
      return { id, text: `${who} — failed${p.note ? `: ${p.note}` : ""}`, tone: "bad" };
    case "apply.blocker_found":
      return {
        id,
        text: `Blocker — ${p.kind ?? ""}${p.field_label ? ` [${p.field_label}]` : ""}${p.detail ? `: ${p.detail}` : ""}`,
        tone: "bad",
      };
    case "apply.waiting_for_packet":
      return { id, text: "Waiting for the résumé packet to finish…", tone: "info" };
    case "apply.ready_for_human":
      return { id, text: "Form ready — review and submit in the application browser.", tone: "warn" };
    case "apply.confirmation_detected":
      return { id, text: "Submission confirmed on the page.", tone: "ok" };
    case "apply.interrupted":
      return { id, text: "Run interrupted — the application browser was closed.", tone: "bad" };
    case "apply.completed":
      return { id, text: "Run completed.", tone: "ok" };
    default:
      return null;
  }
}

function fmtRemaining(ms: number): string {
  if (ms <= 0) return "0:00";
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function ApplierPanel({
  applicationId,
  runId,
  role,
  company,
  onRebind,
  onClose,
}: {
  applicationId: string;
  runId: string;
  role: string;
  company: string;
  /** Retry / Reopen-and-refill starts a fresh run — the parent rebinds the
   *  panel to it (§8.3). */
  onRebind: (newRunId: string) => void;
  onClose: () => void;
}) {
  const runQ = useApplyRun(runId);
  const run = runQ.data;
  const cancel = useCancelApply();
  const attest = useAttestApply();
  const startApply = useStartApply();

  const status: ApplyRunStatus = run?.status ?? "running";
  const phase = phaseInfo(status, run?.phase ?? "");

  // ── Live event feed: seed once per run from the snapshot, then append SSE ──
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const seededRef = useRef<string | null>(null);
  const feedEndRef = useRef<HTMLDivElement | null>(null);

  // A new run (retry rebind) resets the feed; the seed effect re-fills it.
  useEffect(() => {
    seededRef.current = null;
    setFeed([]);
  }, [runId]);

  useEffect(() => {
    if (!run || seededRef.current === runId) return;
    seededRef.current = runId;
    setFeed(seedFeed(run));
  }, [run, runId]);

  useEffect(() => {
    const off = eventBus.subscribe((ev: SSEEvent) => {
      if (ev.type !== "apply") return;
      const p = ev.payload as ApplyEventPayload;
      if (p.run_id !== runId) return;
      const item = formatApplyEvent(p);
      if (item) setFeed((prev) => [...prev, item]);
    });
    return off;
  }, [runId]);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ block: "end" });
  }, [feed]);

  // ── Latest evidence screenshot (screenshot_count-1), via authed blob fetch ──
  const shotCount = run?.screenshot_count ?? 0;
  const [shotUrl, setShotUrl] = useState<string | null>(null);
  const shotUrlRef = useRef<string | null>(null);
  useEffect(() => {
    if (shotCount <= 0) return;
    let cancelled = false;
    api
      .fetchApplyScreenshot(runId, shotCount - 1)
      .then((blob) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        if (shotUrlRef.current) URL.revokeObjectURL(shotUrlRef.current);
        shotUrlRef.current = url;
        setShotUrl(url);
      })
      .catch(() => {
        /* a not-yet-written screenshot is fine — the count-driven refetch retries */
      });
    return () => {
      cancelled = true;
    };
  }, [runId, shotCount]);
  // Revoke the last object URL on unmount.
  useEffect(
    () => () => {
      if (shotUrlRef.current) URL.revokeObjectURL(shotUrlRef.current);
    },
    [],
  );

  // ── Countdown of remaining budget (deadline_at − now), ticking while live ──
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!phase.live) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [phase.live]);
  const remainingMs = run?.deadline_at ? new Date(run.deadline_at).getTime() - now : null;

  // ── Cost line ──
  const usage = run?.usage;
  const costLine = useMemo(() => {
    if (!usage) return "0 calls";
    const parts = [
      `${usage.calls} call${usage.calls === 1 ? "" : "s"}`,
      `${usage.tokens_in.toLocaleString()}+${usage.tokens_out.toLocaleString()} tok`,
    ];
    parts.push(usage.cost_usd != null ? `$${usage.cost_usd.toFixed(4)}` : "cost n/a");
    return parts.join(" · ");
  }, [usage]);

  const canCancel =
    status === "queued" || status === "waiting_for_packet" || status === "running";
  const isRetryable = NON_SUCCESS_TERMINALS.includes(status);
  const okFields = run?.fields.filter((f) => f.ok).length ?? 0;
  const totalFields = run?.fields.length ?? 0;

  async function onRetry() {
    const fresh = await Promise.resolve(
      startApply.mutateAsync({ applicationId, retryOfRunId: runId }),
    );
    if (fresh) onRebind(fresh.id);
  }

  const title = `Applying — ${role} · ${company}`;

  return (
    <Modal
      title={title}
      onClose={onClose}
      width={720}
      headerExtra={
        <span
          data-testid="applier-phase-pill"
          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${PILL_CLS[phase.tone]}`}
        >
          {phase.live ? (
            <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border border-current border-t-transparent" />
          ) : null}
          {phase.label}
        </span>
      }
    >
      <div className="flex h-[76vh] flex-col" data-testid="applier-panel">
        {/* Budget + cost strip */}
        <div className="flex items-center gap-3 border-b border-border bg-surface-2 px-5 py-2 text-[11.5px]">
          <span className="font-mono text-ink-2" data-testid="applier-cost-line">
            {costLine}
          </span>
          {remainingMs != null ? (
            <>
              <span className="text-ink-4">·</span>
              <span
                className="font-mono text-ink-3"
                title="Remaining Apply budget (20-minute total)"
              >
                {phase.live ? `${fmtRemaining(remainingMs)} left` : `budget ${fmtRemaining(Math.max(remainingMs, 0))}`}
              </span>
            </>
          ) : null}
          {run?.final_url ? (
            <a
              href={run.final_url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto truncate text-ink-3 hover:text-ink hover:underline"
              title={run.final_url}
            >
              {run.final_url} ↗
            </a>
          ) : null}
        </div>

        {/* Submitted banner (§8.4 — confirmation detected or user-attested) */}
        {status === "submitted" ? (
          <div className="border-b border-border bg-good-wash px-5 py-2.5 text-[12.5px] font-medium text-good">
            Submitted — this application moved to Applied.
            {run?.submit_evidence ? (
              <span className="ml-1 font-normal text-ink-2">{run.submit_evidence}</span>
            ) : null}
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1">
          {/* Event feed */}
          <div className="flex min-h-0 w-1/2 flex-col border-r border-border">
            <div className="border-b border-border px-4 py-2 font-mono text-[10px] uppercase tracking-wider text-ink-3">
              Activity
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" data-testid="applier-event-feed">
              {runQ.isLoading && feed.length === 0 ? (
                <div className="flex items-center gap-2 text-[12px] text-ink-3">
                  <span className="inline-block h-3 w-3 animate-spin rounded-full border border-border-2 border-t-accent" />
                  Loading run…
                </div>
              ) : feed.length === 0 ? (
                <div className="text-[12px] text-ink-3">No activity yet.</div>
              ) : (
                <ul className="space-y-1.5">
                  {feed.map((it) => (
                    <li key={it.id} className="flex items-start gap-2 text-[12px] text-ink-2">
                      <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${DOT_CLS[it.tone]}`} />
                      <span className="flex-1 leading-snug">{it.text}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div ref={feedEndRef} />
            </div>
          </div>

          {/* Latest evidence screenshot */}
          <div className="flex min-h-0 w-1/2 flex-col">
            <div className="border-b border-border px-4 py-2 font-mono text-[10px] uppercase tracking-wider text-ink-3">
              Latest screenshot
            </div>
            <div
              className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-surface-3 p-3"
              data-testid="applier-screenshot"
            >
              {shotUrl ? (
                <img
                  src={shotUrl}
                  alt="Latest Applier evidence screenshot"
                  className="max-h-full max-w-full rounded-md border border-border object-contain"
                />
              ) : (
                <span className="text-[12px] text-ink-4">No screenshot captured yet.</span>
              )}
            </div>
          </div>
        </div>

        {/* Handoff strip (§8.4) — form ready for the human to submit */}
        {status === "ready_for_human" ? (
          <div
            className="border-t border-border bg-warn-wash px-5 py-3"
            data-testid="applier-handoff-strip"
          >
            <div className="text-[13px] font-semibold text-ink">
              Form ready — review and submit in the application browser
            </div>
            <div className="mt-1 text-[12px] text-ink-2">
              Filled {okFields} of {totalFields} field{totalFields === 1 ? "" : "s"}.
              {run && run.blockers.length > 0
                ? " Couldn’t complete: " +
                  run.blockers
                    .map((b) => `${b.kind}${b.field_label ? ` (${b.field_label})` : ""}`)
                    .join(", ") +
                  "."
                : " finds-you-jobs never submits for you in P1 — check it over, then click the site’s own Submit."}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <button
                data-testid="applier-attest-submitted-btn"
                disabled={attest.isPending}
                onClick={() => attest.mutate({ runId, submitted: true })}
                className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
              >
                I submitted
              </button>
              <button
                data-testid="applier-attest-didnt-btn"
                disabled={attest.isPending}
                onClick={() => attest.mutate({ runId, submitted: false })}
                className="inline-flex h-[30px] items-center rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Didn’t submit
              </button>
            </div>
          </div>
        ) : null}

        {/* Non-success terminal (§8.3) — honest summary + blockers + Retry */}
        {isRetryable ? (
          <div className="border-t border-border bg-bad-wash px-5 py-3">
            <div className="text-[13px] font-semibold text-bad">
              {phase.label}
              {run?.summary ? <span className="ml-1 font-normal text-ink-2">— {run.summary}</span> : null}
            </div>
            {run && run.blockers.length > 0 ? (
              <ul className="mt-1.5 space-y-1 text-[12px] text-ink-2">
                {run.blockers.map((b, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-bad" />
                    <span>
                      {b.kind}
                      {b.field_label ? ` [${b.field_label}]` : ""}
                      {b.detail ? `: ${b.detail}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="mt-3">
              <button
                data-testid="applier-retry-btn"
                disabled={startApply.isPending}
                onClick={() => void onRetry()}
                className="inline-flex h-[30px] items-center gap-1.5 rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
              >
                {startApply.isPending ? (
                  <>
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border border-white/60 border-t-transparent" />
                    Retrying…
                  </>
                ) : (
                  "Retry"
                )}
              </button>
            </div>
          </div>
        ) : null}

        {/* Footer — Cancel while live; Close never cancels the run (§8.2) */}
        <div className="flex items-center justify-end gap-2 border-t border-border bg-surface-2 px-5 py-3">
          {canCancel ? (
            <button
              data-testid="applier-cancel-btn"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(runId)}
              className="mr-auto inline-flex h-[30px] items-center rounded-md border border-bad/40 bg-surface px-3 text-[12px] font-medium text-bad hover:bg-bad-wash disabled:cursor-not-allowed disabled:opacity-50"
            >
              {cancel.isPending ? "Cancelling…" : "Cancel run"}
            </button>
          ) : null}
          <span className="text-[11px] text-ink-4">Closing this panel won’t stop the run.</span>
          <button
            className="h-[30px] rounded-md px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    </Modal>
  );
}
