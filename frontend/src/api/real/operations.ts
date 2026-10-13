// The RealApi methods for operations, the ledger, the scheduler (F-P3).
// Each surface extends the one before it, so RealApi stays 1 class,
// and `real.ts` ends the chain.

import type {
  CostTotals,
  LedgerEntry,
  Operation,
  OperationKind,
  SchedulerStatus,
  Span,
} from "../types";
import {
  type OperationDTO,
  type OperationStubDTO,
  type ScoreRetryDTO,
  type SpanDTO,
  stubOperation,
  toLedgerEntry,
  toOperation,
} from "./mappers";
import { ApiError } from "./transport";
import { DiscoveryApi } from "./discovery";

export class OperationsApi extends DiscoveryApi {
  // ── operations / ledger ────────────────────────────────────────────────
  /** The newest 1000 operations, 20 pages of 50 in the Analytics ledger. Nothing
   *  prunes the table, so this bound keeps the response finite: 501 KB and 97 ms on
   *  a 100k-row ledger, where the cost is the sort, not the row count. */
  async listLedger(): Promise<LedgerEntry[]> {
    const ops = await this.req<OperationDTO[]>("/api/operations?limit=1000");
    return ops.map(toLedgerEntry);
  }

  /** All-time cost totals (FR-SET-07 / US-LOG-01 #2) — live ledger + the pruned
   *  aggregate. Nothing is pruned today, so the aggregate is empty and the live
   *  sum carries every operation ever recorded. */
  async getCostTotals(): Promise<CostTotals> {
    return this.req<CostTotals>("/api/cost/totals");
  }

  /** The Logfire spans for an operation — the Logs drill-down (US-SYS-05). Reads
   *  the local logfire.sqlite store; [] when observability isn't configured. */
  async getOperationSpans(id: string): Promise<Span[]> {
    try {
      const spans = await this.req<SpanDTO[]>(`/api/operations/${id}/spans`);
      return spans.map((s) => ({
        span_id: s.span_id,
        name: s.name,
        operation_id: s.operation_id,
        op_kind: s.op_kind,
        duration_ms: s.duration_ms,
        status: s.status,
        attributes: s.attributes as Record<string, unknown>,
        events: (s.events as { name: string; attributes: Record<string, unknown> }[]) ?? [],
      }));
    } catch (e) {
      // Only a true 404 (no such operation) means "no spans". Anything else
      // (500, auth, network) must propagate — masking it as [] rendered a
      // transient failure as an empty drill-down (F-L7, same pattern as getJob).
      if (e instanceof ApiError && e.status === 404) return [];
      throw e;
    }
  }

  /** How many jobs have spent their whole AI-scoring budget with no score
   *  (S-C24). 0 means the ledger's Retry-scoring button stays hidden. */
  async retryableScoreCount(): Promise<number> {
    const d = await this.req<ScoreRetryDTO>("/api/scoring/retryable");
    return d.count;
  }

  /** Give every stuck job its scoring budget back, so the next scheduler tick
   *  re-plans it. Returns how many were reset. Enqueues nothing here: the tick
   *  does the work, batched and off the request. */
  async retryScoring(): Promise<number> {
    const d = (await this.json("POST", "/api/scoring/retry", {})) as ScoreRetryDTO;
    return d.reset;
  }

  /** Whether background work is running, and whether a degraded boot is why it
   *  is not. The shell starts the backend without its scheduler after 3 runs in
   *  a row end the same bad way (D27). */
  async schedulerStatus(): Promise<SchedulerStatus> {
    return this.req<SchedulerStatus>("/api/system/scheduler");
  }

  /** Turn background work back on after a degraded boot. Idempotent. */
  async resumeScheduler(): Promise<SchedulerStatus> {
    return (await this.json("POST", "/api/system/scheduler/resume", {})) as SchedulerStatus;
  }

  /** Re-run a failed op with its original inputs (US-LOG-01 Retry). */
  async retryOperation(id: string): Promise<Operation> {
    return stubOperation(
      (await this.json("POST", `/api/operations/${id}/retry`, {})) as OperationStubDTO,
    );
  }

  /** Stop a queued/running op (F-M7 Stop button). 202 with the honest
   *  post-cancel state; 404 unknown; 409 when nothing can honestly cancel. */
  async cancelOperation(id: string): Promise<Operation> {
    return stubOperation(
      (await this.json("POST", `/api/operations/${id}/cancel`, {})) as OperationStubDTO,
    );
  }

  async getOperation(id: string): Promise<Operation | undefined> {
    try {
      return toOperation(await this.req<OperationDTO>(`/api/operations/${id}`));
    } catch (e) {
      // Only a true 404 means "no such operation" — everything else propagates
      // (F-L7, same pattern as getJob).
      if (e instanceof ApiError && e.status === 404) return undefined;
      throw e;
    }
  }

  async enqueueOperation(kind: OperationKind): Promise<Operation> {
    return stubOperation(
      (await this.json("POST", `/api/operations/${kind}`, {})) as OperationStubDTO,
    );
  }
}
