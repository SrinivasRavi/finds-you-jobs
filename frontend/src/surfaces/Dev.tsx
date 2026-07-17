// Dev tools (US-DEV-01) — local fault-injection surface for testing the app's
// failure handling without a live scrape/LLM/LinkedIn session, alongside the
// commit-2 handshake/SSE diagnostic (sidecar health, event-stream state, and a
// snapshot refetch after every reconnect). Single-user local app on the user's
// own machine; these actions never touch a hosted backend. Hidden from the left
// rail (maintainer note 2026-07-09); reachable at /dev.

import { useCallback, useEffect, useState } from "react";

import { fetchHealth, getSidecarInfo, type SidecarInfo } from "../api/client";
import { eventBus, type StreamState } from "../api/events";
import { fetchOperations } from "../api/operations";
import {
  useDevExpireCookie,
  useDevFailRunning,
  useDevSeedApplication,
} from "../api/queries";
import type { DevResult } from "../api/types";

const POLL_MS = 2000;

type Status =
  | { kind: "connecting" }
  | { kind: "connected"; info: SidecarInfo }
  | { kind: "unavailable"; message: string };

// Handshake + SSE diagnostic (grew out of the commit-2 skeleton shell): proves
// the /healthz path and the SSE transport (live / reconnecting, with a snapshot
// refetch after every reconnect) end to end.
function StatusCard() {
  const [status, setStatus] = useState<Status>({ kind: "connecting" });
  const [stream, setStream] = useState<StreamState>("connecting");
  const [opsCount, setOpsCount] = useState<number | null>(null);

  const refetchSnapshot = useCallback(() => {
    fetchOperations()
      .then((ops) => setOpsCount(ops.length))
      .catch(() => setOpsCount(null));
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const info = await getSidecarInfo();
        const health = await fetchHealth(info);
        if (!cancelled && health.status === "ok") {
          setStatus({ kind: "connected", info });
          return;
        }
      } catch (err) {
        if (!cancelled) {
          setStatus({
            kind: "unavailable",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      }
    }

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    // Events missed while disconnected are never replayed — refetch the
    // snapshot on every transition into `live` (initial connect + reconnects).
    return eventBus.subscribe(null, (state) => {
      setStream(state);
      if (state === "live") refetchSnapshot();
    });
  }, [refetchSnapshot]);

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="text-[13px] font-semibold text-ink">Sidecar & event stream</div>
      <p className="mt-1 text-[12px] text-ink-3">
        Handshake + SSE transport health — proves the /healthz path and the event stream end to end.
      </p>
      <div className="mt-3 space-y-1 text-[12.5px] text-ink-2">
        <p data-testid="sidecar-status">
          {status.kind === "connecting" && "connecting to sidecar…"}
          {status.kind === "connected" && "connected"}
          {status.kind === "unavailable" && "sidecar unavailable"}
        </p>
        {status.kind === "connected" && (
          <p data-testid="sidecar-port">sidecar healthy on 127.0.0.1:{status.info.port}</p>
        )}
        {status.kind === "unavailable" && <p data-testid="sidecar-error">{status.message}</p>}
        <p>
          event stream: <span data-testid="sse-status">{stream}</span>
        </p>
        <p data-testid="ops-count">
          {opsCount === null
            ? "operations ledger unavailable"
            : `operations recorded: ${opsCount}`}
        </p>
      </div>
    </div>
  );
}

function ActionCard({
  title,
  desc,
  button,
  danger,
  onRun,
  result,
  pending,
}: {
  title: string;
  desc: string;
  button: string;
  danger?: boolean;
  onRun: () => void;
  result?: DevResult;
  pending: boolean;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="text-[13px] font-semibold text-ink">{title}</div>
      <p className="mt-1 text-[12px] text-ink-3">{desc}</p>
      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={onRun}
          disabled={pending}
          data-testid={`dev-run-${title.toLowerCase().replace(/\s+/g, "-")}`}
          className={
            "rounded-md px-3 py-1.5 text-[12.5px] font-medium text-white disabled:opacity-60 " +
            (danger ? "bg-bad hover:opacity-90" : "bg-accent hover:bg-accent-ink")
          }
        >
          {pending ? "Running…" : button}
        </button>
        {result ? (
          <span
            className={`font-mono text-[11.5px] ${result.ok ? "text-good" : "text-bad"}`}
            data-testid="dev-result"
          >
            {result.ok ? "✓" : "✗"}{" "}
            {result.note ??
              result.detail ??
              (result.count != null
                ? `${result.count} operation(s) failed`
                : result.application_id
                  ? `seeded application ${result.application_id.slice(0, 8)}`
                  : "done")}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function Dev() {
  const expire = useDevExpireCookie();
  const failRunning = useDevFailRunning();
  const seed = useDevSeedApplication();
  const [results, setResults] = useState<Record<string, DevResult>>({});

  function run(key: string, mutate: { mutateAsync: () => Promise<DevResult> }) {
    void mutate.mutateAsync().then((r) => setResults((prev) => ({ ...prev, [key]: r })));
  }

  return (
    <>
      <header className="flex min-h-[48px] items-center border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">Dev tools</h1>
        <span className="ml-3 text-[12px] text-ink-3">
          Local fault injection — for testing failure handling
        </span>
      </header>
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto grid w-full max-w-2xl gap-4">
          <StatusCard />
          <ActionCard
            title="Expire LinkedIn cookie"
            desc="Strips the li_at cookie from the saved session without changing the connected status — so the app still believes it's connected and the NEXT LinkedIn action (discover/send) fails on auth. Use it to test how an in-flight action handles a session that dies midway."
            button="Expire session cookie"
            danger
            pending={expire.isPending}
            result={results.expire}
            onRun={() => run("expire", expire)}
          />
          <ActionCard
            title="Fail running operations"
            desc="Marks every in-flight operation failed with the boot-recovery note — simulates the app crashing mid-generation, so you can exercise the Analytics 'App restarted while generating — Retry' path."
            button="Fail in-flight ops"
            danger
            pending={failRunning.isPending}
            result={results.failRunning}
            onRun={() => run("failRunning", failRunning)}
          />
          <ActionCard
            title="Seed a sample application"
            desc="Creates a Job + a Saved application so the Tracker has a card to drag, generate a packet on, or apply — without a live scrape or score."
            button="Seed Saved application"
            pending={seed.isPending}
            result={results.seed}
            onRun={() => run("seed", seed)}
          />
        </div>
      </main>
    </>
  );
}
