// updateSettings merges a 1-field patch onto what the sidecar holds. A pane used
// to send its whole group from a cached copy, so a stale copy wrote old values
// back: the OTLP headers were lost under load (journey-settings.spec.ts).

import { describe, expect, it, vi } from "vitest";

import { RealApi } from "../real";
import type { Settings } from "../types";

function apiWith(uiState: Record<string, unknown>) {
  const api = new RealApi();
  const posted: Record<string, unknown>[] = [];
  const raw = api as unknown as {
    req: (path: string) => Promise<unknown>;
    json: (method: string, path: string, body: Record<string, unknown>) => Promise<unknown>;
  };
  vi.spyOn(raw, "req").mockResolvedValue({ preferences: { thresholds: {}, ui_state: uiState } });
  vi.spyOn(raw, "json").mockImplementation(async (_m, _p, body) => {
    posted.push(body);
    return {};
  });
  vi.spyOn(api, "getSettings").mockResolvedValue({} as Settings);
  return { api, posted };
}

describe("updateSettings sends only the fields a patch names", () => {
  it("an observability field keeps the stored headers and endpoint", async () => {
    const { api, posted } = apiWith({
      otlp_enabled: true,
      otlp_endpoint: "http://collector:4318",
      otlp_headers: { "x-team": "e2e" },
      retention_days: 7,
    });
    await api.updateSettings({ observability: { retention_days: 5 } });
    expect(posted[0].ui_state).toEqual({
      otlp_enabled: true,
      otlp_endpoint: "http://collector:4318",
      otlp_headers: { "x-team": "e2e" },
      retention_days: 5,
    });
  });

  it("a lifecycle window keeps the other stored windows", async () => {
    const { api, posted } = apiWith({ lifecycle: { sent_ghosted_days: 21, contact_purge_days: 120 } });
    await api.updateSettings({ lifecycle: { trashed_jobs_purge_days: 9 } });
    expect(posted[0].ui_state).toEqual({
      lifecycle: { sent_ghosted_days: 21, contact_purge_days: 120, trashed_jobs_purge_days: 9 },
    });
  });
});
