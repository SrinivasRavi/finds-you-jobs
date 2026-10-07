// Drawings 13, 14 and 15 (sections 9.10, 9.11, 9.12): the cost-ledger row that
// can finally open the document it paid for, the Lifecycle setting whose copy
// has to admit that a hand-added job never ages out, and the Trash confirm that
// says what "delete forever" means when identity is the description's hash.

import { useState } from "react";

import { Avatar } from "../../shell/Avatar";
import { COPY } from "./mock";

interface LedgerRow {
  at: string;
  kind: string;
  state: string;
  model: string;
  usd: string;
  subject: string;
  trailing: "resume" | "score" | null;
}

const LEDGER_ROWS: LedgerRow[] = [
  {
    at: "14:22",
    kind: "tailor",
    state: "succeeded",
    model: "claude-sonnet-5",
    usd: "$0.04",
    subject: "Senior Software Engineer · Acme",
    trailing: "resume",
  },
  {
    at: "14:19",
    kind: "score",
    state: "succeeded",
    model: "keyword",
    usd: "$0.00",
    subject: "Senior Software Engineer · Acme",
    trailing: "score",
  },
];

export function LedgerPreview({ onToast }: { onToast: (note: string) => void }) {
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="overflow-hidden rounded-xl border border-border bg-surface">
        <table className="w-full table-fixed border-collapse text-[12.5px]">
          <thead>
            <tr className="border-b border-border text-left text-ink-3">
              <th className="px-3 py-2 font-medium">Operation</th>
              <th className="w-[96px] px-3 py-2 font-medium">State</th>
              <th className="w-[104px] px-3 py-2 font-medium">Started</th>
              <th className="w-[130px] px-3 py-2 font-medium">Model</th>
              <th className="w-[88px] px-3 py-2 text-right font-medium">Cost</th>
            </tr>
          </thead>
          <tbody data-testid="urlless-ledger">
            {LEDGER_ROWS.map((row) => (
              <tr key={row.at} className="border-b border-border/60 align-top">
                <td className="px-3 py-2">
                  <div className="flex min-w-0 items-baseline gap-x-2">
                    <span className="shrink-0 text-[11px] text-ink-3">{row.kind}</span>
                    <span className="min-w-0 truncate font-medium text-ink">{row.subject}</span>
                  </div>
                  {row.trailing === "resume" ? (
                    <button
                      type="button"
                      data-testid="urlless-ledger-view-resume"
                      onClick={() => onToast("Opened the tailored resume this row paid for.")}
                      className="mt-1 rounded-md border border-border-2 bg-surface px-2 py-0.5 text-[11px] text-ink-2 hover:bg-surface-3"
                    >
                      {COPY.ledger.viewResume}
                    </button>
                  ) : (
                    <div className="mt-1 text-[11px] text-ink-3">scored 72 · nothing saved</div>
                  )}
                </td>
                <td className="px-3 py-2">
                  <span className="rounded-full bg-good-wash px-2 py-0.5 text-[10px] text-good">
                    {row.state}
                  </span>
                </td>
                <td className="px-3 py-2 text-[11px] text-ink-3">{row.at}</td>
                <td className="truncate px-3 py-2 text-ink-2">{row.model}</td>
                <td className="px-3 py-2 text-right text-ink-2">{row.usd}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 max-w-[560px] text-[11.5px] leading-snug text-ink-4">
        On main the subject column reads "(none)" for a job-less operation and the markdown it
        generated has nowhere to go. Both rows above resolve their subject and the tailor row opens
        its document.
      </p>
    </div>
  );
}

export function LifecyclePreview() {
  const [days, setDays] = useState(14);
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-[620px] rounded-xl border border-border bg-surface p-5">
        <h3 className="text-[13px] font-semibold text-ink">Contact and data lifecycle</h3>
        <div className="mt-4 flex items-center gap-3" data-testid="urlless-lifecycle-row">
          <div className="flex-1">
            <div className="text-[13px] font-medium text-ink">{COPY.lifecycle.label}</div>
            <div className="text-[12px] leading-snug text-ink-3">{COPY.lifecycle.hint}</div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              data-testid="urlless-lifecycle-days"
              className="rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink"
            >
              {[7, 14, 30, 60].map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            <span className="text-[11.5px] text-ink-3">{COPY.lifecycle.days}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

interface TrashRow {
  id: string;
  title: string;
  company: string;
  hasLink: boolean;
}

const TRASH_ROWS: TrashRow[] = [
  { id: "t1", title: "Platform Engineer", company: "Rubrik", hasLink: true },
  { id: "t2", title: "Senior Software Engineer", company: "Acme", hasLink: false },
];

export function TrashPreview({ onToast }: { onToast: (note: string) => void }) {
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [gone, setGone] = useState<string[]>([]);
  const rows = TRASH_ROWS.filter((r) => !gone.includes(r.id));
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-[560px] overflow-hidden rounded-xl border border-border bg-surface">
        <div className="border-b border-border px-5 py-4 text-[16px] font-semibold text-ink">
          Deleted Jobs
        </div>
        <ul className="space-y-2 px-5 py-4" data-testid="urlless-trash-list">
          {rows.map((row) => (
            <li key={row.id} className="rounded-md border border-border px-3 py-2">
              <div className="flex items-center gap-3">
                <Avatar name={row.company} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium text-ink">{row.title}</div>
                  <div className="truncate text-[11px] text-ink-3">
                    {row.company} · Deleted recently
                  </div>
                </div>
                {confirmId === row.id ? (
                  <>
                    <button
                      type="button"
                      data-testid="urlless-trash-confirm"
                      onClick={() => {
                        setGone((g) => [...g, row.id]);
                        setConfirmId(null);
                        onToast(
                          row.hasLink
                            ? "Deleted forever. The posting URL is tombstoned."
                            : "Deleted forever. That description is tombstoned too.",
                        );
                      }}
                      className="rounded-md border border-bad/40 bg-bad px-2 py-1 text-[11.5px] font-medium text-white hover:opacity-90"
                    >
                      {COPY.trash.deleteForever}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmId(null)}
                      className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                    >
                      {COPY.trash.cancel}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                    >
                      Undo
                    </button>
                    <button
                      type="button"
                      data-testid="urlless-trash-arm"
                      onClick={() => setConfirmId(row.id)}
                      className="rounded-md border border-bad/40 px-2 py-1 text-[11.5px] text-bad hover:bg-bad-wash"
                    >
                      {COPY.trash.deleteForever}
                    </button>
                  </>
                )}
              </div>
              {!row.hasLink && confirmId === row.id ? (
                <p
                  className="mt-2 text-[11.5px] leading-snug text-warn"
                  data-testid="urlless-trash-text-warning"
                >
                  ⚠ {COPY.trash.textWarning}
                </p>
              ) : null}
            </li>
          ))}
          {rows.length === 0 ? <li className="text-[13px] text-ink-3">Trash is empty.</li> : null}
        </ul>
        <div className="border-t border-border px-5 py-3 text-[11.5px] text-ink-3">
          {COPY.trash.retention}
        </div>
      </div>
    </div>
  );
}
