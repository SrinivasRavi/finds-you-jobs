// Analytics (§12 / FR-SET-07 + US-LOG-01) — the merged cost-and-usage + logs
// surface. Left 25%: aggregate cost tiles + per-kind spend from the operations
// ledger (the cost source of truth, architecture §10). Right 75%: the operations
// ledger table with a per-operation span drill-down (US-SYS-05 / A6), a Started
// timestamp, per-agent filter chips, and a friendly "App restarted while
// generating — retry?" affordance over the boot-recovery note (US-LOG-01).

import { Fragment, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useCancelOperation,
  useCostTotals,
  useDiscoveryAnalytics,
  useLedger,
  useOperationSpans,
  useRetryOperation,
} from "../api/queries";
import type { CostTotals, LedgerEntry, OperationKind, OperationState, Span } from "../api/types";
import { formatWhen } from "../shell/datetime";

const STATE_CLS: Record<OperationState, string> = {
  succeeded: "bg-good-wash text-good",
  failed: "bg-bad-wash text-bad",
  running: "bg-accent-wash text-accent",
  queued: "bg-surface-3 text-ink-3",
  cancelled: "bg-surface-3 text-ink-3",
};

// The verbatim boot-recovery note stays in the DB + flight recorder (NFR-SIDE-04);
// the Logs UI recognizes it and shows a plain-language line instead (US-LOG-01).
const RESTART_NOTE_MARKER = "boot recovery";

// Filter groups (US-LOG-01 #4). These cover EVERY operation kind so no row is
// ever silently hidden; `groupOf` falls back to "system" for any future kind.
// No chips selected = show all (standard filter).
const GROUPS: { key: string; label: string; kinds: OperationKind[] }[] = [
  // Scraper is its own group (maintainer directive 2026-07-18 #5): scans are
  // the discovery workload, not system plumbing.
  { key: "scraper", label: "analytics.groups.scraper", kinds: ["scan"] },
  { key: "scoring", label: "analytics.groups.scoring", kinds: ["score"] },
  { key: "tailoring", label: "analytics.groups.tailoring", kinds: ["tailor"] },
  { key: "cover", label: "analytics.groups.cover", kinds: ["cover"] },
  {
    key: "networking",
    label: "analytics.groups.networking",
    kinds: ["discover", "draft", "send", "linkedin_login", "linkedin_search", "archive_stale_contacts"],
  },
  { key: "apply", label: "analytics.groups.apply", kinds: ["apply", "extract", "prep"] },
  { key: "system", label: "analytics.groups.system", kinds: ["cleanup_trash", "contact_sync", "archive_stale_contacts", "watch_company"] },
];

const KIND_TO_GROUP: Record<string, string> = Object.fromEntries(
  GROUPS.flatMap((g) => g.kinds.map((k) => [k, g.key])),
);

/** The filter group a kind belongs to — unknown kinds fall into "system" so
 *  nothing is ever dropped from the ledger view. */
function groupOf(kind: string): string {
  return KIND_TO_GROUP[kind] ?? "system";
}

const PAGE_SIZE = 50; // US-LOG-01 #2 — client pagination; retention keeps ~5 pages.

function num(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}

// ─── left panel: cost dashboard ──────────────────────────────────────────────

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-3">
      <div className="text-[11px] font-medium text-ink-3">{label}</div>
      <div className="mt-1 font-mono text-[20px] font-semibold text-ink">{value}</div>
      {sub ? <div className="text-[11px] text-ink-3">{sub}</div> : null}
    </div>
  );
}

// All-time totals (FR-SET-07 / US-LOG-01 #2). Read from /api/cost/totals — live
// ledger + the pruned-ops aggregate — so the tiles survive the ~250-op ledger
// retention. Falls back to zeros while the query loads.
function CostPanel({ totals }: { totals: CostTotals | undefined }) {
  const { t } = useTranslation();
  const total = totals?.usd ?? 0;
  const ops = totals?.operations ?? 0;
  const failed = totals?.failed ?? 0;
  const byKind = totals?.by_kind ?? {};
  const maxKind = Math.max(0.01, ...Object.values(byKind));
  return (
    <div className="space-y-4" data-testid="cost-tiles">
      <Tile label={t("analytics.tiles.totalSpend")} value={`$${total.toFixed(2)}`} sub={t("analytics.tiles.allTime")} />
      <Tile label={t("analytics.tiles.operations")} value={String(ops)} sub={t("analytics.tiles.failedSub", { failed })} />
      <Tile label={t("analytics.tiles.avgPerOp")} value={`$${(ops ? total / ops : 0).toFixed(2)}`} sub={t("analytics.tiles.acrossAllKinds")} />
      <div className="rounded-xl border border-border bg-surface p-3">
        <div className="mb-2 text-[11px] font-medium text-ink-3">
          {t("analytics.tiles.spendByKind")}
        </div>
        <div className="space-y-1.5">
          {(Object.keys(byKind) as OperationKind[]).map((kind) => (
            <div key={kind} className="flex items-center gap-2">
              <span className="w-12 font-mono text-[10.5px] text-ink-3">{kind}</span>
              <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full rounded-full bg-accent"
                  style={{ width: `${(byKind[kind] / maxKind) * 100}%` }}
                />
              </div>
              <span className="w-12 text-right font-mono text-[10.5px] text-ink-2">
                ${byKind[kind].toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── right panel: operations ledger ──────────────────────────────────────────

function SpanDetail({ operationId }: { operationId: string }) {
  const { t } = useTranslation();
  const { data: spans = [], isLoading } = useOperationSpans(operationId);
  if (isLoading) return <div className="px-3 py-3 text-[12px] text-ink-3">{t("analytics.span.loading")}</div>;
  if (spans.length === 0) {
    return (
      <div className="px-3 py-3 text-[12px] text-ink-3" data-testid="span-empty">
        {t("analytics.span.empty")}
      </div>
    );
  }
  return (
    <div className="space-y-3 px-3 py-3" data-testid="span-detail">
      {spans.map((s: Span) => {
        const a = s.attributes;
        const cost = num(a.cost_usd);
        return (
          <div key={s.span_id} className="rounded-lg border border-border bg-surface-2 p-3">
            <div className="mb-2 flex items-center gap-2">
              <span className="font-mono text-[11px] font-semibold text-ink">{s.name}</span>
              <span
                className={`rounded-full px-2 py-0.5 font-mono text-[10px] ${
                  s.status === "ERROR" ? "bg-bad-wash text-bad" : "bg-good-wash text-good"
                }`}
              >
                {/* OTel's default span status is "UNSET" (no explicit status) —
                    for our completed spans that means OK; show that, not jargon. */}
                {s.status === "ERROR" ? t("analytics.span.error") : t("analytics.span.ok")}
              </span>
              <span className="ml-auto font-mono text-[11px] text-ink-2">
                {t("analytics.span.ms", { value: s.duration_ms.toFixed(0) })}
              </span>
            </div>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11.5px] sm:grid-cols-3">
              <Field label={t("analytics.span.model")} value={(a.model as string) ?? "—"} />
              <Field label={t("analytics.span.cost")} value={cost != null ? `$${cost.toFixed(4)}` : "—"} />
              <Field
                label={t("analytics.span.latency")}
                value={
                  num(a.latency_ms) != null
                    ? t("analytics.span.seconds", { value: (num(a.latency_ms)! / 1000).toFixed(1) })
                    : "—"
                }
              />
              <Field label={t("analytics.span.tokensIn")} value={num(a.tokens_in) != null ? String(a.tokens_in) : "—"} />
              <Field label={t("analytics.span.tokensOut")} value={num(a.tokens_out) != null ? String(a.tokens_out) : "—"} />
              <Field label={t("analytics.span.engineCalls")} value={num(a.internal_calls) != null ? String(a.internal_calls) : "—"} />
            </dl>
            {a.error ? (
              <div className="mt-2 font-mono text-[11px] text-bad" data-testid="span-error">
                {String(a.error)}
              </div>
            ) : null}
            {s.events.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {s.events.map((ev, i) => (
                  <span
                    key={i}
                    className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-ink-3"
                  >
                    {ev.name}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-ink-4">{label}</dt>
      <dd className="font-mono text-ink-2">{value}</dd>
    </div>
  );
}

// Interactive, non-generic paths the ledger cannot re-enqueue (backend 422s).
const NON_RETRYABLE: OperationKind[] = ["apply", "linkedin_login"];

/** Retry for a failed row (US-LOG-01) — rendered in the State cell, right under
 *  the FAILED pill (2026-07-12 beta feedback on placement). Re-enqueues the op
 *  with its stored input snapshot. */
function RetryButton({ entry }: { entry: LedgerEntry }) {
  const { t } = useTranslation();
  const retry = useRetryOperation();
  const canRetry =
    entry.state === "failed" && !entry.retried_as && !NON_RETRYABLE.includes(entry.kind);
  if (!canRetry) return null;
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        retry.mutate(entry.id);
      }}
      disabled={retry.isPending}
      data-testid="log-retry"
      className="mt-1 block rounded-md border border-accent bg-accent px-2 py-0.5 text-[11px] font-medium text-white hover:bg-accent-ink disabled:opacity-60"
    >
      {retry.isPending ? t("analytics.ledger.retrying") : t("analytics.ledger.retry")}
    </button>
  );
}

// Mirrors the backend's CANCELLABLE_RUNNING_KINDS (registry/operations.py):
// the only kinds that poll the cancel token while RUNNING. Queued ops of any
// kind can always be cancelled server-side. This is a render heuristic, not a
// gate — a race (the op advancing under us) lands a 409 that surfaces through
// the global MutationErrorBanner and the ledger refresh makes the row honest.
const CANCELLABLE_RUNNING_KINDS: OperationKind[] = ["score", "tailor", "cover"];

// `apply` cancellation is never a generic ledger Stop — even a queued apply is
// cancelled through its own apply-run flow (the ApplierPanel's dedicated Cancel,
// applier.md §8.2), which lands the run honestly. A generic "Stop" here would be
// a semantically odd second affordance, so the ledger never renders one for it.
const STOP_EXCLUDED_KINDS: OperationKind[] = ["apply"];

/** Stop for a queued/running row (F-M7) — rendered in the State cell like
 *  RetryButton, so the affordance sits right under the state pill. */
function StopButton({ entry }: { entry: LedgerEntry }) {
  const { t } = useTranslation();
  const cancel = useCancelOperation();
  const canStop =
    !STOP_EXCLUDED_KINDS.includes(entry.kind) &&
    (entry.state === "queued" ||
      (entry.state === "running" && CANCELLABLE_RUNNING_KINDS.includes(entry.kind)));
  if (!canStop) return null;
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        cancel.mutate(entry.id);
      }}
      disabled={cancel.isPending}
      data-testid="log-stop"
      className="mt-1 block rounded-md border border-border-2 bg-surface px-2 py-0.5 text-[11px] font-medium text-ink-2 hover:bg-surface-3 disabled:opacity-60"
    >
      {cancel.isPending ? t("analytics.ledger.stopping") : t("analytics.ledger.stop")}
    </button>
  );
}

/** The error cell: the boot-recovery note gets friendly copy; other failures
 *  show the verbatim error (de-emphasized once retried). The Retry button
 *  itself lives in the State cell (RetryButton). Long verbatim errors WRAP
 *  (break-words) — the ledger must never scroll sideways (2026-08-03). */
function ErrorCell({ entry }: { entry: LedgerEntry }) {
  const { t } = useTranslation();
  if (!entry.error) {
    // A succeeded send our own caps refused in-band: the state pill is honest
    // ("Succeeded" — the op ran fine) but the row must say nothing went out.
    if (entry.refusal) {
      return (
        <div className="mt-0.5 break-words font-mono text-[11px] text-warn" data-testid="log-refused">
          {t("analytics.ledger.refused", { reason: entry.refusal })}
        </div>
      );
    }
    return null;
  }
  if (entry.error.includes(RESTART_NOTE_MARKER)) {
    return (
      <div className="mt-0.5 flex items-center gap-2 text-[11.5px]" data-testid="log-restart">
        <span className="text-ink-2">{t("analytics.ledger.restarted")}</span>
      </div>
    );
  }
  return (
    <div
      className={
        "mt-0.5 break-words font-mono text-[11px] " + (entry.retried_as ? "text-ink-4" : "text-bad")
      }
      data-testid="log-error"
    >
      {entry.error}
    </div>
  );
}

// The per-kind noun for a subject's `count` (US-LOG-01 legibility): the number
// itself is backend data; the noun is UI chrome and stays in the locale files.
const COUNT_KEYS: Partial<Record<OperationKind, string>> = {
  discover: "analytics.subject.contactsFound",
  scan: "analytics.subject.newJobs",
  linkedin_search: "analytics.subject.newJobs",
  contact_sync: "analytics.subject.contactsChecked",
  archive_stale_contacts: "analytics.subject.contactsArchived",
  cleanup_trash: "analytics.subject.itemsCleaned",
};

/** The Operation cell: the kind tag, then WHAT the op acted on — the subject
 *  label (hyperlinked when the backend resolved an external URL — the
 *  referrals-UI external-link pattern), the count phrase, the context line.
 *  All entity text is verbatim backend data; everything wraps (no sideways
 *  scroll). The full message/detail text lives in the EXPANDED row. */
function SubjectCell({ entry }: { entry: LedgerEntry }) {
  const { t } = useTranslation();
  const s = entry.subject;
  const countKey = COUNT_KEYS[entry.kind];
  // Singleton-entity kinds carry no backend subject; name the entity here.
  const label =
    s?.label || (entry.kind === "extract" ? t("analytics.subject.masterResume") : "");
  // `linkedin_search` context is the mode enum ("fresh"/"next") — map it to
  // copy; any unknown value renders verbatim rather than vanishing.
  const context =
    entry.kind === "linkedin_search" && s?.context
      ? t(`analytics.subject.searchMode.${s.context}`, { defaultValue: s.context })
      : (s?.context ?? null);
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="font-mono text-[11px] text-ink-3">{entry.kind}</span>
        {s?.count != null && countKey ? (
          <span className="text-[11px] text-ink-3" data-testid="log-count">
            {t(countKey, { count: s.count })}
          </span>
        ) : null}
      </div>
      {label ? (
        s?.href ? (
          <a
            href={s.href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            data-testid="log-subject"
            className="break-words text-ink underline-offset-2 hover:text-accent hover:underline"
          >
            {label}
          </a>
        ) : (
          <div className="break-words text-ink" data-testid="log-subject">
            {label}
          </div>
        )
      ) : null}
      {context ? (
        <div className="break-words text-[11px] text-ink-3" data-testid="log-context">
          {context}
        </div>
      ) : null}
    </>
  );
}

// Discovery tab (approved-plan #7): per-source efficacy from existing records
// — stored jobs × saves × scores per family, plus recent-scan fetch/keep/
// error/latency aggregates. Lets the user (and the maintainer dogfooding
// sources) see which families actually yield for their role/location, and
// which to untick in Settings → Discovery sources.
function DiscoveryPanel() {
  const { t } = useTranslation();
  const { data } = useDiscoveryAnalytics();
  if (!data) return null;
  return (
    <div className="space-y-3" data-testid="discovery-panel">
      <div className="text-[11px] font-medium text-ink-4">
        {t("analytics.discovery.sourceEfficacy", { count: data.scans })}
      </div>
      {data.sources.length === 0 ? (
        <div className="text-[12px] text-ink-3">
          {t("analytics.discovery.empty")}
        </div>
      ) : (
        data.sources.map((s) => (
          <div
            key={s.id}
            data-testid={`discovery-source-${s.id}`}
            className="rounded-lg border border-border bg-surface p-2.5"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-[12.5px] font-medium text-ink">{s.label}</span>
              <span className="font-mono text-[12px] text-ink-2">
                {t("analytics.discovery.jobs", { jobs: s.jobs })}
              </span>
            </div>
            <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] text-ink-3">
              <span>{t("analytics.discovery.saved", { saved: s.saved })}</span>
              <span>
                {t("analytics.discovery.avgScore", {
                  score: s.avg_score !== null ? Math.round(s.avg_score) : "—",
                })}
              </span>
              <span>
                {t("analytics.discovery.kept", { kept: s.kept, fetched: s.fetched })}
              </span>
              <span className={s.errors > 0 ? "text-warn" : ""}>
                {t("analytics.discovery.errors", { errors: s.errors })}
              </span>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

export function Analytics() {
  const { t } = useTranslation();
  const { data: ledger = [] } = useLedger();
  const { data: costTotals } = useCostTotals();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [active, setActive] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const [tab, setTab] = useState<"costs" | "discovery">("costs");

  const filtered = useMemo(() => {
    // No group selected → show all; else keep rows whose group is selected.
    if (active.size === 0) return ledger;
    return ledger.filter((e) => active.has(groupOf(e.kind)));
  }, [ledger, active]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  function toggle(key: string) {
    setPage(0);
    setActive((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <>
      <header className="flex min-h-[48px] items-center border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">{t("nav.analytics")}</h1>
        <span className="ml-3 text-[12px] text-ink-3">
          {t("analytics.header.subtitle")}
        </span>
      </header>
      <main className="flex min-h-0 flex-1 gap-5 overflow-hidden p-5">
        {/* Left 25% — Costs | Discovery tabs (approved-plan #7) */}
        <aside className="w-1/4 min-w-[200px] shrink-0 overflow-y-auto">
          {/* Costs | Discovery — same tab language as the Settings prompt tabs
              (maintainer 2026-07-23 #7), laid side by side. */}
          <div
            className="mb-3 flex gap-1.5 border-b border-border pb-2"
            role="tablist"
            data-testid="analytics-tabs"
          >
            {(
              [
                ["costs", "analytics.tabs.costs"],
                ["discovery", "analytics.tabs.discovery"],
              ] as const
            ).map(([tabKey, label]) => (
              <button
                key={tabKey}
                role="tab"
                onClick={() => setTab(tabKey)}
                data-testid={`analytics-tab-${tabKey}`}
                aria-selected={tab === tabKey}
                className={
                  "rounded-md px-3 py-1.5 text-[12.5px] font-medium " +
                  (tab === tabKey ? "bg-accent-wash text-accent-ink" : "text-ink-2 hover:bg-surface-3")
                }
              >
                {t(label)}
              </button>
            ))}
          </div>
          {tab === "costs" ? <CostPanel totals={costTotals} /> : <DiscoveryPanel />}
        </aside>

        {/* Right 75% — operations ledger */}
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="mb-3 flex items-center gap-1.5" data-testid="agent-filters">
            <span className="mr-1 text-[11.5px] text-ink-4">{t("analytics.ledger.show")}</span>
            {GROUPS.map((c) => (
              <button
                key={c.key}
                onClick={() => toggle(c.key)}
                data-testid={`agent-chip-${c.key}`}
                aria-pressed={active.has(c.key)}
                className={
                  "h-7 rounded-full border px-2.5 text-[11.5px] transition " +
                  (active.has(c.key)
                    ? "border-accent bg-accent text-white"
                    : "border-border-2 bg-surface text-ink-2 hover:bg-surface-3")
                }
              >
                {t(c.label)}
              </button>
            ))}
          </div>
          {/* table-fixed + wrapping cells: the ledger must FIT the window —
              no horizontal scrolling at the app's default widths (maintainer
              directive 2026-08-03). Every bounded column has an explicit
              width; the Operation column absorbs the rest and wraps. */}
          <div
            className="min-h-0 flex-1 overflow-auto rounded-xl border border-border bg-surface"
            data-testid="ledger-scroll"
          >
            <table className="w-full table-fixed border-collapse text-[12.5px]">
              <thead>
                <tr className="border-b border-border text-left text-ink-3">
                  <th className="w-7 px-2 py-2" />
                  <th className="px-3 py-2 font-medium">{t("analytics.ledger.operation")}</th>
                  <th className="w-[96px] px-3 py-2 font-medium">{t("analytics.ledger.state")}</th>
                  <th className="w-[104px] px-3 py-2 font-medium">{t("analytics.ledger.started")}</th>
                  <th className="w-[116px] px-3 py-2 font-medium">{t("analytics.ledger.model")}</th>
                  <th className="w-[88px] px-3 py-2 text-right font-medium">{t("analytics.ledger.cost")}</th>
                </tr>
              </thead>
              <tbody data-testid="logs-table">
                {pageRows.map((e: LedgerEntry) => {
                  const open = expandedId === e.id;
                  return (
                    <Fragment key={e.id}>
                      <tr
                        className="cursor-pointer border-b border-border/60 align-top hover:bg-surface-2"
                        data-testid="log-row"
                        onClick={() => setExpandedId(open ? null : e.id)}
                      >
                        <td className="px-2 py-2 font-mono text-[10px] text-ink-4">
                          {open ? "▾" : "▸"}
                        </td>
                        <td className="px-3 py-2">
                          <SubjectCell entry={e} />
                          <ErrorCell entry={e} />
                        </td>
                        <td className="px-3 py-2">
                          {e.state === "failed" && e.retried_as ? (
                            <span
                              className="rounded-full bg-surface-3 px-2 py-0.5 font-mono text-[10px] capitalize text-ink-3"
                              data-testid="log-retried-pill"
                            >
                              {t("analytics.ledger.retried")}
                            </span>
                          ) : (
                            <span
                              className={`rounded-full px-2 py-0.5 font-mono text-[10px] capitalize ${STATE_CLS[e.state] ?? "bg-surface-3 text-ink-3"}`}
                            >
                              {e.state}
                            </span>
                          )}
                          <RetryButton entry={e} />
                          <StopButton entry={e} />
                        </td>
                        <td
                          className="px-3 py-2 font-mono text-[11px] text-ink-3"
                          data-testid="log-started"
                        >
                          {formatWhen(e.started_at ?? e.created_at, "timestamp")}
                        </td>
                        <td
                          className="truncate px-3 py-2 text-ink-2"
                          title={e.model ?? undefined}
                        >
                          {e.model ?? "—"}
                        </td>
                        <td
                          className="px-3 py-2 text-right font-mono text-ink-2"
                          title={e.usd == null ? t("analytics.ledger.costUnknown") : undefined}
                        >
                          <div>{e.usd != null ? `$${e.usd.toFixed(2)}` : "—"}</div>
                          {e.latency_ms ? (
                            <div className="text-[10.5px] text-ink-4">
                              {t("analytics.ledger.seconds", {
                                value: (e.latency_ms / 1000).toFixed(1),
                              })}
                            </div>
                          ) : null}
                        </td>
                      </tr>
                      {open ? (
                        <tr className="border-b border-border/60 bg-surface-2/40">
                          <td />
                          <td colSpan={5}>
                            {e.subject?.detail ? (
                              <div className="px-3 pt-3">
                                <div className="mb-1 text-[11px] font-medium text-ink-4">
                                  {t("analytics.ledger.detailHeading")}
                                </div>
                                <div
                                  className="whitespace-pre-wrap break-words rounded-lg border border-border bg-surface-2 p-3 text-[12px] text-ink-2"
                                  data-testid="log-detail"
                                >
                                  {e.subject.detail}
                                </div>
                              </div>
                            ) : null}
                            <SpanDetail operationId={e.id} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {/* Pager (US-LOG-01 #2). Retention keeps ~5 pages; older auto-deleted. */}
          <div
            className="mt-3 flex items-center justify-between text-[11.5px] text-ink-3"
            data-testid="logs-pager"
          >
            <span>
              {filtered.length === 0
                ? t("analytics.pager.empty")
                : t("analytics.pager.range", {
                    from: safePage * PAGE_SIZE + 1,
                    to: Math.min((safePage + 1) * PAGE_SIZE, filtered.length),
                    total: filtered.length,
                  })}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={safePage === 0}
                data-testid="logs-prev"
                className="rounded-md border border-border-2 bg-surface px-2 py-1 text-ink-2 hover:bg-surface-3 disabled:opacity-40"
              >
                {t("analytics.pager.prev")}
              </button>
              <span className="font-mono">
                {safePage + 1} / {pageCount}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                disabled={safePage >= pageCount - 1}
                data-testid="logs-next"
                className="rounded-md border border-border-2 bg-surface px-2 py-1 text-ink-2 hover:bg-surface-3 disabled:opacity-40"
              >
                {t("analytics.pager.next")}
              </button>
            </div>
          </div>
        </section>
      </main>
    </>
  );
}
