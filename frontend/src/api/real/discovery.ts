// The RealApi methods for discovery sources, keys, watchlist, analytics (F-P3).
// Each surface extends the one before it, so RealApi stays 1 class,
// and `real.ts` ends the chain.

import type {
  DiscoveryAnalytics,
  DiscoveryCredential,
  DiscoverySource,
  ScheduleRow,
  WatchCompanyResult,
  WatchlistEntry,
} from "../types";
import { SettingsApi } from "./settings";

export class DiscoveryApi extends SettingsApi {
  // ── /api/discovery/* (source toggles, BYO keys, watchlist, analytics) ────
  async listDiscoverySources(): Promise<DiscoverySource[]> {
    return this.req<DiscoverySource[]>("/api/discovery/sources");
  }
  /** Flip one source (string) or a whole Settings section (string[] — the
   *  section-title checkboxes; one atomic POST server-side). */
  async toggleDiscoverySource(
    idOrIds: string | string[],
    enabled: boolean,
  ): Promise<DiscoverySource[]> {
    const body = Array.isArray(idOrIds)
      ? { ids: idOrIds, enabled }
      : { id: idOrIds, enabled };
    return (await this.json("POST", "/api/discovery/sources", body)) as DiscoverySource[];
  }
  async listDiscoveryCredentials(): Promise<DiscoveryCredential[]> {
    return this.req<DiscoveryCredential[]>("/api/discovery/credentials");
  }
  async saveDiscoveryCredential(id: string, key: string): Promise<DiscoveryCredential[]> {
    return (await this.json("POST", "/api/discovery/credentials", {
      id,
      key,
    })) as DiscoveryCredential[];
  }
  async deleteDiscoveryCredential(id: string): Promise<DiscoveryCredential[]> {
    const res = await this.req<DiscoveryCredential[]>(`/api/discovery/credentials/${id}`, {
      method: "DELETE",
    });
    return res;
  }
  /** One-shot logged-in LinkedIn job search (discovery-expansion #6). Returns
   *  the enqueued op; results land in the normal feed. 403 (toggle off) / 409
   *  (not connected / no continuable cursor) surface as errors. `mode: "next"`
   *  continues the last Fresh search's snapshot from its saved offset. */
  async linkedinSearch(
    mode: "fresh" | "next" = "fresh",
  ): Promise<{ id: string; kind: string; state: string }> {
    return (await this.json(
      "POST",
      "/api/linkedin/search",
      { mode },
    )) as { id: string; kind: string; state: string };
  }
  async watchCompany(input: {
    url?: string;
    job_id?: string;
    company?: string;
  }): Promise<WatchCompanyResult> {
    return (await this.json("POST", "/api/discovery/watchlist", input)) as WatchCompanyResult;
  }
  async getSchedules(): Promise<ScheduleRow[]> {
    return this.req<ScheduleRow[]>("/api/schedules");
  }
  async getWatchlist(): Promise<WatchlistEntry[]> {
    const d = await this.req<{ entries: WatchlistEntry[] }>("/api/discovery/watchlist");
    return d.entries;
  }
  async unwatchCompany(url: string): Promise<boolean> {
    const d = await this.req<{ removed: boolean }>(
      `/api/discovery/watchlist?url=${encodeURIComponent(url)}`,
      { method: "DELETE" },
    );
    return d.removed;
  }
  async getDiscoveryAnalytics(): Promise<DiscoveryAnalytics> {
    return this.req<DiscoveryAnalytics>("/api/discovery/analytics");
  }
}
