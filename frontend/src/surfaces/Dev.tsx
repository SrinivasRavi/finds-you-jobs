// Dev status surface (grew out of the commit-2 skeleton shell): proves the handshake + /healthz path and the SSE transport
// (live / reconnecting, with a snapshot refetch after every reconnect) end to
// end. The real product UI replaces this as later roadmap commits land;
// nothing here is a design surface.

import { useCallback, useEffect, useState } from "react";

import { fetchHealth, getSidecarInfo, type SidecarInfo } from "../api/client";
import { eventBus, type StreamState } from "../api/events";
import { fetchOperations } from "../api/operations";

const POLL_MS = 2000;

type Status =
  | { kind: "connecting" }
  | { kind: "connected"; info: SidecarInfo }
  | { kind: "unavailable"; message: string };

export function Dev() {
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
    <main
      style={{
        fontFamily: "system-ui, sans-serif",
        maxWidth: "40rem",
        margin: "4rem auto",
        padding: "0 1rem",
      }}
    >
      <h1>finds-you-jobs</h1>
      <p>Desktop shell skeleton — Tauri + Python sidecar + React.</p>
      <p data-testid="sidecar-status">
        {status.kind === "connecting" && "connecting to sidecar…"}
        {status.kind === "connected" && "connected"}
        {status.kind === "unavailable" && "sidecar unavailable"}
      </p>
      {status.kind === "connected" && (
        <p data-testid="sidecar-port">
          sidecar healthy on 127.0.0.1:{status.info.port}
        </p>
      )}
      {status.kind === "unavailable" && (
        <p data-testid="sidecar-error">{status.message}</p>
      )}
      <p>
        event stream: <span data-testid="sse-status">{stream}</span>
      </p>
      <p data-testid="ops-count">
        {opsCount === null
          ? "operations ledger unavailable"
          : `operations recorded: ${opsCount}`}
      </p>
    </main>
  );
}
