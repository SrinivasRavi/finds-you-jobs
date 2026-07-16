// Skeleton shell: proves the handshake + /healthz path end to end (shell →
// sidecar → UI) and renders an honest connection state. The real product UI
// replaces this as later roadmap commits land; nothing here is a design
// surface.

import { useEffect, useState } from "react";

import { fetchHealth, getSidecarInfo, type SidecarInfo } from "./api/client";

const POLL_MS = 2000;

type Status =
  | { kind: "connecting" }
  | { kind: "connected"; info: SidecarInfo }
  | { kind: "unavailable"; message: string };

export default function App() {
  const [status, setStatus] = useState<Status>({ kind: "connecting" });

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
    </main>
  );
}
