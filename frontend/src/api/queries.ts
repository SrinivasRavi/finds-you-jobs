// TanStack Query hooks over the (sidecar-backed) client + SSE invalidation.
// Server state lives in Query; the SSE bus invalidates the relevant keys so
// feed deltas / operation progress flow into the UI (architecture section 6:
// "TanStack Query, invalidated by SSE events").
//
// Job Board / Dev status page / main.tsx guard hooks, plus the applications/
// tracker hooks (restored — `/api/applications` now exists) and the
// networking hooks (restored 2026-07-16 — the referral-outreach backend now
// exists: /api/contacts, /api/jobs/{id}/referrals/*, /api/referrals/*,
// /api/linkedin/*). Apply/prep/packet-prompts/spans hooks return with their
// own commits, once the sidecar grows that surface.

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect } from "react";

import { eventBus, type StreamState } from "./events";
import { api } from "./index";
import type {
  Application,
  BoardPage,
  CompanyConfirmPick,
  ContactInput,
  DiscoverySource,
  EngineSaveInput,
  Job,
  JobDraft,
  LinkedInSessionState,
  ManualApplicationInput,
  NetContact,
  Priority,
  ReachOutInput,
  Settings,
  Stage,
} from "./types";

export const qk = {
  jobs: ["jobs"] as const,
  board: ["board"] as const,
  scanProgress: ["scanProgress"] as const,
  trash: ["trash"] as const,
  applications: ["applications"] as const,
  activity: ["activity"] as const,
  networking: ["networking"] as const,
  archived: ["archived"] as const,
  profile: ["profile"] as const,
  onboarding: ["onboarding"] as const,
  settings: ["settings"] as const,
  discoverySources: ["discoverySources"] as const,
  watchlist: ["watchlist"] as const,
  schedules: ["schedules"] as const,
  discoveryCredentials: ["discoveryCredentials"] as const,
  discoveryAnalytics: ["discoveryAnalytics"] as const,
  prompts: ["prompts"] as const,
  ledger: ["ledger"] as const,
  costTotals: ["costTotals"] as const,
  spans: ["spans"] as const,
  contacts: ["contacts"] as const,
  archivedContacts: ["archivedContacts"] as const,
  referralCandidates: ["referralCandidates"] as const,
  referralQuota: ["referralQuota"] as const,
  linkedinSession: ["linkedinSession"] as const,
  applyRun: ["applyRun"] as const,
  applyRuns: ["applyRuns"] as const,
};

// ─── Queries ─────────────────────────────────────────────────────────────────

export function useJobs() {
  return useQuery({ queryKey: qk.jobs, queryFn: () => api.listJobs() });
}
/** The paginated Job Board feed (FR-JB-02) — infinite scroll over 50-row pages
 *  with a live total + real last-scan meta (FR-JB-10). Saved-excluded server-side.
 *  `listQ`/`textQ` (FR-JB-13) are server-side search filters keyed into the
 *  query, so clearing a search falls back to the cached unfiltered feed. */
export function useBoard(listQ = "", textQ = "") {
  return useInfiniteQuery({
    queryKey: [...qk.board, listQ, textQ],
    queryFn: ({ pageParam }) => Promise.resolve(api.getBoard(pageParam, listQ, textQ)),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.jobs.length, 0);
      return loaded < last.total ? all.length : undefined;
    },
  });
}
/** Board-level scan + scoring progress (observed-issue #2) — the Rescan status
 *  pill. Kept fresh by the SSE bridge (scan/score events, throttled) AND a modest
 *  poll that runs ONLY while a cycle is active (`scan_running || score_pending>0`),
 *  so the "M of N" ticks up smoothly even if an SSE hint is dropped, and drops to
 *  zero network traffic once idle. */
export function useScanProgress() {
  return useQuery({
    queryKey: qk.scanProgress,
    queryFn: () => api.getScanProgress(),
    refetchInterval: (query) => {
      const d = query.state.data;
      return d && (d.scan_running || d.score_pending > 0) ? 1500 : false;
    },
  });
}
/** Trashed jobs (US-JB-11) — the Trash modal's own source, off the board feed. */
export function useTrash() {
  return useQuery({ queryKey: qk.trash, queryFn: () => Promise.resolve(api.listTrash()) });
}
// Restored: the Tracker's own list + the "Deleted Applications" archive.
export function useApplications() {
  return useQuery({ queryKey: qk.applications, queryFn: () => api.listApplications() });
}
export function useArchived() {
  return useQuery({ queryKey: qk.archived, queryFn: () => api.listArchived() });
}
/** Real Activity log for one application (US-TR-03 / FR-TR-03). */
export function useApplicationActivity(id: string | null) {
  return useQuery({
    queryKey: [...qk.activity, id],
    queryFn: () => Promise.resolve(api.getApplicationActivity(id as string)),
    enabled: id != null,
  });
}
/** The role's referral contacts for the detail-modal Networking tab (US-TR-03),
 *  restored 2026-07-16. */
export function useApplicationNetworking(id: string | null) {
  return useQuery({
    queryKey: [...qk.networking, id],
    queryFn: () => Promise.resolve(api.getApplicationNetworking(id as string)),
    enabled: id != null,
  });
}
export function useProfile() {
  return useQuery({ queryKey: qk.profile, queryFn: () => api.getProfile() });
}
/** First-launch guard (FR-OB-01): whether a MasterProfile exists ⟺ onboarded.
 *  Retries forever: this query gates the whole app, and while the sidecar is
 *  still booting (seconds on a cold Windows start) the right behavior is
 *  "keep showing the boot splash and self-heal", never an error dead-end. */
export function useMasterProfileExists() {
  return useQuery({
    queryKey: qk.onboarding,
    queryFn: () => Promise.resolve(api.hasMasterProfile()),
    retry: true,
    retryDelay: (attempt) => Math.min(500 * 2 ** attempt, 3_000),
  });
}
export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: () => api.getSettings() });
}
/** The Discovery-sources catalog (Settings toggles) — every adapter family the
 *  scraper ships, with the user's per-family opt-outs. */
export function useDiscoverySources() {
  return useQuery({
    queryKey: qk.discoverySources,
    queryFn: () => api.listDiscoverySources(),
  });
}
export function useToggleDiscoverySource() {
  const qc = useQueryClient();
  return useMutation({
    // `id` flips one source; `ids` flips a whole section (the section-title
    // checkboxes) in one atomic POST.
    mutationFn: ({ id, ids, enabled }: { id?: string; ids?: string[]; enabled: boolean }) =>
      api.toggleDiscoverySource(ids ?? id ?? "", enabled),
    // Optimistic: the checkbox flips instantly (it's a controlled input — a
    // POST round-trip delay reads as a dead click); rollback on error.
    onMutate: async ({ id, ids, enabled }) => {
      await qc.cancelQueries({ queryKey: qk.discoverySources });
      const prev = qc.getQueryData<DiscoverySource[]>(qk.discoverySources);
      if (prev) {
        const targets = new Set(ids ?? (id ? [id] : []));
        qc.setQueryData(
          qk.discoverySources,
          prev.map((s) => (targets.has(s.id) ? { ...s, enabled } : s)),
        );
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.discoverySources, ctx.prev);
    },
    // The POST returns the authoritative catalog — write it into the cache
    // (family toggles cascade to actor rows server-side).
    onSuccess: (rows) => {
      qc.setQueryData(qk.discoverySources, rows);
      qc.invalidateQueries({ queryKey: qk.settings }); // portals_config changed
    },
  });
}
/** BYO scraper keys (Apify / Brave) — Settings → Discovery sources. */
export function useDiscoveryCredentials() {
  return useQuery({
    queryKey: qk.discoveryCredentials,
    queryFn: () => api.listDiscoveryCredentials(),
  });
}
export function useSaveDiscoveryCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, key }: { id: string; key: string }) =>
      api.saveDiscoveryCredential(id, key),
    onSuccess: (rows) => {
      qc.setQueryData(qk.discoveryCredentials, rows);
      // Saving an Apify/Brave key seeds its [[sources]] entries server-side.
      qc.invalidateQueries({ queryKey: qk.discoverySources });
      qc.invalidateQueries({ queryKey: qk.settings });
    },
  });
}
export function useDeleteDiscoveryCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteDiscoveryCredential(id),
    onSuccess: (rows) => {
      qc.setQueryData(qk.discoveryCredentials, rows);
      qc.invalidateQueries({ queryKey: qk.discoverySources });
    },
  });
}
/** One-shot logged-in LinkedIn job search (discovery-expansion #6). Invalidates
 *  the feed on success so the newly-found rows appear; the session query (which
 *  carries the pagination cursor for the Next-page button) repaints via the
 *  op's `linkedin` SSE events. `mode: "next"` continues the last Fresh search. */
export function useLinkedinSearch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mode: "fresh" | "next" = "fresh") => api.linkedinSearch(mode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.jobs });
      qc.invalidateQueries({ queryKey: qk.board });
    },
  });
}
/** Watch a company's board — adds a [[sources]] row (approved-plan #4). */
export function useWatchCompany() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { url?: string; job_id?: string; company?: string }) =>
      api.watchCompany(input),
    // No optimistic cache-faking (maintainer 2026-07-22: a fake pre-flip is a
    // one-off patch, not a pattern). Speed comes from the server remembering
    // resolved boards (rewatch skips the probe); the toggle shows an honest
    // in-flight label for the rare genuinely-slow first probe.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.discoverySources });
      qc.invalidateQueries({ queryKey: qk.settings });
      qc.invalidateQueries({ queryKey: qk.watchlist });
    },
    // Both call sites (board watch toggle, finder-prefs tracked roster) render
    // the failure inline — no global banner.
    meta: { errorHandledLocally: true },
  });
}
/** Background schedules — the preferences modal shows the scan schedule's
 *  next_due_at so the cadence is visibly real. */
export function useSchedules() {
  return useQuery({ queryKey: qk.schedules, queryFn: () => api.getSchedules() });
}
/** The tracked-companies roster (Job finder preferences) — `watched` rows. */
export function useWatchlist() {
  return useQuery({ queryKey: qk.watchlist, queryFn: () => api.getWatchlist() });
}
export function useUnwatchCompany() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (url: string) => api.unwatchCompany(url),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.watchlist });
      qc.invalidateQueries({ queryKey: qk.discoverySources });
      qc.invalidateQueries({ queryKey: qk.settings });
    },
    meta: { errorHandledLocally: true },
  });
}
/** Per-source efficacy aggregates — the Analytics Discovery tab. */
export function useDiscoveryAnalytics() {
  return useQuery({
    queryKey: qk.discoveryAnalytics,
    queryFn: () => api.getDiscoveryAnalytics(),
  });
}
/** The operations ledger — the Analytics table + cost source of truth (section 10). */
export function useLedger() {
  return useQuery({ queryKey: qk.ledger, queryFn: () => api.listLedger() });
}
/** All-time cost totals for the Analytics cost tiles (FR-SET-07 / US-LOG-01 #2):
 *  live ledger + the pruned aggregate, so the tiles stay honest as an install ages. */
export function useCostTotals() {
  return useQuery({ queryKey: qk.costTotals, queryFn: () => api.getCostTotals() });
}
/** The Logfire spans for one operation — the Logs drill-down (US-SYS-05). Only
 *  fetched when a row is expanded (`enabled`). */
export function useOperationSpans(id: string | null) {
  return useQuery({
    queryKey: [...qk.spans, id],
    queryFn: () => api.getOperationSpans(id as string),
    enabled: id != null,
  });
}
// ─── User-editable LLM prompts (FR-SET-11) ─────────────────────────────────
// Each module's skill markdown, exposed + editable in Settings. The list is
// server-driven; save/reset refresh the query so the row's "edited" badge +
// textarea re-render.
export function usePrompts() {
  return useQuery({ queryKey: qk.prompts, queryFn: () => Promise.resolve(api.listPrompts()) });
}

// ─── Mutations ───────────────────────────────────────────────────────────────

/** Invalidate every board-feed view (list, paginated board, trash) at once. */
export function invalidateFeed(qc: QueryClient): void {
  qc.invalidateQueries({ queryKey: qk.jobs });
  qc.invalidateQueries({ queryKey: qk.board });
  qc.invalidateQueries({ queryKey: qk.trash });
}

/** Invalidate the referral-roster views at once: the find-referrals popup's
 *  candidate list, the contact kanban, and the Tracker card's Referrals slot. */
export function invalidateRoster(qc: QueryClient): void {
  qc.invalidateQueries({ queryKey: qk.referralCandidates });
  qc.invalidateQueries({ queryKey: qk.contacts });
  qc.invalidateQueries({ queryKey: qk.applications });
}

/** Invalidate both contact rosters — the live kanban and the "Deleted Contacts"
 *  recovery modal (an archive/restore moves a row between them). */
export function invalidateContactLists(qc: QueryClient): void {
  qc.invalidateQueries({ queryKey: qk.contacts });
  qc.invalidateQueries({ queryKey: qk.archivedContacts });
}

/** Invalidate the Tracker's card list + the detail-modal Activity tab — every
 *  mutation that writes an Activity event (FR-TR-03/04) needs both. */
export function invalidateTracker(qc: QueryClient): void {
  qc.invalidateQueries({ queryKey: qk.applications });
  qc.invalidateQueries({ queryKey: qk.activity });
}

export function useSaveJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      saved,
      generate_resume,
      generate_cover,
      generate_prep,
    }: {
      id: string;
      saved: boolean;
      generate_resume?: boolean;
      generate_cover?: boolean;
      generate_prep?: boolean;
    }) =>
      Promise.resolve(
        api.setJobSaved(id, saved, { generate_resume, generate_cover, generate_prep }),
      ),
    // Optimistic (2026-07-11 Save-lag fix): the POST is ~10 ms and every
    // on-Save op is queued server-side — the UI updates its own caches
    // instead of refetching every loaded board page.
    onMutate: async ({ id, saved }) => {
      await qc.cancelQueries({ queryKey: qk.board });
      qc.setQueryData<Job[] | undefined>(qk.jobs, (jobs) =>
        jobs?.map((j) => (j.id === id ? { ...j, saved } : j)),
      );
      if (saved) {
        // Prefix-matched (FR-JB-13): the board cache is keyed per search query,
        // so the saved row must leave every cached variant, not just the
        // unfiltered one.
        qc.setQueriesData<InfiniteData<BoardPage> | undefined>(
          { queryKey: qk.board },
          (data) =>
            data
              ? {
                  ...data,
                  pages: data.pages.map((pg) => ({
                    ...pg,
                    jobs: pg.jobs.filter((j) => j.id !== id),
                    total: Math.max(0, pg.total - 1),
                  })),
                }
              : data,
        );
      }
    },
    // Roll back the optimistic update on a real failure and let the caller's
    // error handling surface the honest message.
    onError: () => invalidateFeed(qc),
    // Restored: `setJobSaved` now really persists — refresh the Tracker's own
    // list too (a new card, or one card fewer on un-save).
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: qk.applications });
      // Un-save is the rare path — a full feed refresh there is fine.
      if (!vars.saved) invalidateFeed(qc);
    },
  });
}

export function useTrashJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, trashed }: { id: string; trashed: boolean }) =>
      Promise.resolve(api.setJobBoardState(id, trashed ? "trashed" : "active")),
    onSuccess: () => invalidateFeed(qc),
  });
}

/** Empty Trash (US-JB-11 / FR-SYS-04): tombstone + remove every Trashed job. */
export function useEmptyTrash() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(api.emptyTrash()),
    onSuccess: () => invalidateFeed(qc),
  });
}

/** Delete forever (US-JB-11): tombstone + remove one Trashed job. */
export function useTombstoneJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => Promise.resolve(api.tombstoneJob(id)),
    onSuccess: () => invalidateFeed(qc),
  });
}

/** Add-by-URL step 1: fetch the pasted URL → editable draft (no persist).
 *  Every call site renders the failure inline — no global banner. */
export function useJobPreview() {
  return useMutation({
    mutationFn: (url: string): Promise<JobDraft> => Promise.resolve(api.previewJob(url)),
    meta: { errorHandledLocally: true },
  });
}

/** Add-by-URL step 2: persist the (edited) draft. Failure renders inline. */
export function useAddJobByUrl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (draft: JobDraft): Promise<Job> => Promise.resolve(api.addJobByUrl(draft)),
    onSuccess: () => invalidateFeed(qc),
    meta: { errorHandledLocally: true },
  });
}

/** "Add a job application" (FR-TR manual-add): log an externally-applied job as
 *  an `origin=manual` tracker card, with optional resume/cover uploads.
 *  Failure renders inline in the add-application modal. */
export function useAddManualApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ManualApplicationInput): Promise<Application> =>
      Promise.resolve(api.createManualApplication(input)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.applications });
      invalidateFeed(qc);
    },
    meta: { errorHandledLocally: true },
  });
}

/** Fire an on-demand scan (zero-LLM). The board refreshes as jobs land. */
export function useTriggerScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(api.enqueueOperation("scan")),
    // Kick the scan-progress query so the Rescan pill flips to "Scanning" (and
    // its active-only poll starts) without waiting for the first SSE tick.
    onSuccess: () => {
      invalidateFeed(qc);
      qc.invalidateQueries({ queryKey: qk.scanProgress });
    },
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (master_md: string) => Promise.resolve(api.updateProfile(master_md)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.profile }),
  });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Settings>) => Promise.resolve(api.updateSettings(patch)),
    onSuccess: (_r, patch) => {
      qc.invalidateQueries({ queryKey: qk.settings });
      // Switching to keyword mode backfills scores server-side in the same
      // request — refetch the board so the grey scores appear immediately.
      if (patch.scoring_mode !== undefined) invalidateFeed(qc);
    },
  });
}

// BYOK provider surface (FR-SET-06 / US-SET-07). Verify is fire-and-read (no
// cache write); save/delete refresh the settings query so the tiles re-render.
export function useVerifyEngine() {
  return useMutation({
    mutationFn: (input: EngineSaveInput) => Promise.resolve(api.verifyEngine(input)),
  });
}

export function useSaveEngine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: EngineSaveInput) => Promise.resolve(api.saveEngine(input)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings }),
  });
}

export function useDeleteEngine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (provider: string) => Promise.resolve(api.deleteEngine(provider)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings }),
  });
}

// User-editable LLM prompts (FR-SET-11) — save/reset refresh the prompts query.
export function useSetPrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { kind: string; markdown: string }) =>
      Promise.resolve(api.setPrompt(input.kind, input.markdown)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.prompts }),
  });
}

export function useResetPrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (kind: string) => Promise.resolve(api.resetPrompt(kind)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.prompts }),
  });
}

/** Retry a failed operation from the Analytics ledger (US-LOG-01). */
export function useRetryOperation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => Promise.resolve(api.retryOperation(id)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.ledger });
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.jobs });
    },
  });
}

/** Stop a queued/running operation from the Analytics ledger (F-M7). A 409
 *  ("nothing to honestly cancel" — e.g. the op went terminal, or a running
 *  kind that never observes the token) rejects and surfaces through the
 *  global MutationErrorBanner flow; the ledger refresh on settle makes the
 *  row honest again either way. */
export function useCancelOperation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => Promise.resolve(api.cancelOperation(id)),
    onSuccess: () => {
      // A cancelled score/tailor/cover leaves a card slot or board score
      // un-generated — same refresh set as Retry, which re-creates them.
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.jobs });
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.ledger });
    },
  });
}

// ─── Dev tools (local fault injection — US-DEV-01) ───────────────────────────

export function useDevExpireCookie() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(api.devExpireCookie()),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.linkedinSession }),
  });
}
export function useDevFailRunning() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(api.devFailRunning()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.ledger });
      qc.invalidateQueries({ queryKey: qk.applications });
    },
  });
}
export function useDevSeedApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(api.devSeedApplication()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.jobs });
    },
  });
}

// ─── Applications / Tracker mutations (restored) ─────────────────────────────

export function useMoveApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, stage }: { id: string; stage: Stage }) =>
      Promise.resolve(api.moveApplication(id, stage)),
    // A move writes an Activity event (FR-TR-03) → refresh the detail-modal tab.
    onSuccess: () => invalidateTracker(qc),
  });
}

export function useSetPriority() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, priority }: { id: string; priority: Priority }) =>
      Promise.resolve(api.setPriority(id, priority)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.applications }),
  });
}

export function useUpdateApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<Application> }) =>
      Promise.resolve(api.updateApplication(id, patch)),
    // A notes edit / column move writes an Activity event (FR-TR-04) — refresh
    // the detail-modal Activity tab so it appears without a manual reload.
    onSuccess: () => invalidateTracker(qc),
  });
}

export function useArchiveApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => Promise.resolve(api.archiveApplication(id)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.archived });
      qc.invalidateQueries({ queryKey: qk.activity });
    },
  });
}

export function useUnarchiveApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => Promise.resolve(api.unarchiveApplication(id)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.archived });
      qc.invalidateQueries({ queryKey: qk.activity });
    },
  });
}

/** Permanent per-row delete in the Deleted Applications modal. The job may
 *  resurface in Discover (same semantics as the retention purge) — hence the
 *  feed invalidation. */
export function useDeleteApplicationForever() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => Promise.resolve(api.deleteApplicationForever(id)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.archived });
      qc.invalidateQueries({ queryKey: qk.applications });
      invalidateFeed(qc);
    },
  });
}

export function useReturnToBoard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => Promise.resolve(api.returnToBoard(id)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.applications });
      invalidateFeed(qc);
    },
  });
}

export function useGeneratePacket() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      fail,
      resume = true,
      cover = true,
      guidance = "",
    }: {
      id: string;
      fail?: boolean;
      resume?: boolean;
      cover?: boolean;
      guidance?: string;
    }) => Promise.resolve(api.generatePacket(id, fail, { resume, cover }, guidance)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.applications }),
  });
}

/** Persist an edited variant + the Approve-and-Save flip (US-RES-02 / FR-RES-02). */
export function usePatchArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      kind,
      markdown,
      approved,
    }: {
      id: string;
      kind: "tailored" | "cover";
      markdown?: string;
      approved?: boolean;
    }) => Promise.resolve(api.patchArtifact(id, kind, { markdown, approved })),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.applications }),
  });
}

/** Attach an external resume/cover FILE to an application (the Upload button on
 *  the Tailored resume / Cover letter editors). Failure renders inline. */
export function useAttachDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      kind,
      file,
    }: {
      id: string;
      kind: "tailored_resume" | "cover_letter";
      file: File;
    }) => Promise.resolve(api.attachDocument(id, kind, file)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.applications }),
    meta: { errorHandledLocally: true },
  });
}

/** Detach the attached resume/cover file (the ✕ on the chip). */
export function useRemoveDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      kind,
    }: {
      id: string;
      kind: "tailored_resume" | "cover_letter";
    }) => Promise.resolve(api.removeDocument(id, kind)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.applications }),
    meta: { errorHandledLocally: true },
  });
}

// ─── Apply Runs (the agentic Applier — applier-as-built.md section 8/section 9) ─────────────────────

/** All Apply Runs for one application (section 8.3 — the immutable attempt history). */
export function useApplyRuns(applicationId: string | null) {
  return useQuery({
    queryKey: [...qk.applyRuns, applicationId],
    queryFn: () => api.listApplyRuns(applicationId as string),
    enabled: applicationId != null,
  });
}

/** One Apply Run's live snapshot for the companion panel. Poll-light: the run
 *  is refetched only when an `apply` SSE event for THIS run_id lands, or a
 *  terminal apply operation fires — never on a timer (section 9.2). Seeds/keeps the
 *  panel honest whether it was open the whole time or reopened after the fact. */
export function useApplyRun(runId: string | null) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: [...qk.applyRun, runId],
    queryFn: () => api.getApplyRun(runId as string),
    enabled: runId != null,
  });
  useEffect(() => {
    if (!runId) return;
    return eventBus.subscribe((ev) => {
      if (ev.type === "apply") {
        if (ev.payload.run_id === runId) {
          qc.invalidateQueries({ queryKey: [...qk.applyRun, runId] });
        }
        return;
      }
      // A terminal apply operation is the authoritative end-of-run signal — the
      // run row is settled by then, so re-read its final snapshot.
      if (ev.type === "operation") {
        const p = ev.payload;
        if (p.kind === "apply" && (p.state === "succeeded" || p.state === "failed")) {
          qc.invalidateQueries({ queryKey: [...qk.applyRun, runId] });
        }
      }
    });
  }, [runId, qc]);
  return query;
}

/** Start an Apply Run (section 8.1) — no pre-confirm; the click IS the action.
 *  `retryOfRunId` starts a fresh run linked to the prior one (section 8.3). Seeds the
 *  new run into the cache so the companion binds instantly. */
export function useStartApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ applicationId, retryOfRunId }: { applicationId: string; retryOfRunId?: string }) =>
      Promise.resolve(api.startApply(applicationId, retryOfRunId)),
    onSuccess: (run) => {
      qc.setQueryData([...qk.applyRun, run.id], run);
      qc.invalidateQueries({ queryKey: qk.applyRuns });
      qc.invalidateQueries({ queryKey: qk.applications });
    },
  });
}

/** Cooperative cancel (section 8.2) — lands the run as `interrupted`. */
export function useCancelApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => Promise.resolve(api.cancelApplyRun(runId)),
    onSuccess: (run) => {
      qc.setQueryData([...qk.applyRun, run.id], run);
      qc.invalidateQueries({ queryKey: qk.applications });
    },
  });
}

/** The human's post-handoff attestation (section 8.4). A `true` advances the card to
 *  Applied — refresh applications + the Activity tab. */
export function useAttestApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, submitted }: { runId: string; submitted: boolean }) =>
      Promise.resolve(api.attestApplyRun(runId, submitted)),
    onSuccess: (run) => {
      qc.setQueryData([...qk.applyRun, run.id], run);
      invalidateTracker(qc);
    },
  });
}

// ─── Networking (Track N3) — restored 2026-07-16 from the prior repo's
// queries.ts: the referral-outreach backend now exists. ─────────────────────

export function useContacts(company?: string) {
  return useQuery({
    queryKey: [...qk.contacts, company ?? "all"],
    queryFn: () => api.listContacts(company),
  });
}

/** The "Deleted Contacts" recovery roster — archived contacts only (US-NW-02). */
export function useArchivedContacts() {
  return useQuery({ queryKey: qk.archivedContacts, queryFn: () => api.listArchivedContacts() });
}

export function useReferralQuota() {
  return useQuery({ queryKey: qk.referralQuota, queryFn: () => api.getReferralQuota() });
}

export function useLinkedInSession() {
  return useQuery({ queryKey: qk.linkedinSession, queryFn: () => api.getLinkedInSession() });
}

/** Start the headed LinkedIn login (US-SET-06). The connect control itself
 *  lives in Settings (not built on this repo yet); this hook is restored so
 *  Settings can wire it up directly. SSE `linkedin` events repaint the status
 *  chip + pill; the op finishing flips the session to `valid`. */
export function useConnectLinkedIn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(api.connectLinkedIn()),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.linkedinSession }),
  });
}

/** Every endpoint that returns the authoritative session lands it the same way:
 *  write it into the session cache, then refresh the referral quota — the caps
 *  it counts against ride on that session. */
function applyLinkedInSession(qc: QueryClient, session: LinkedInSessionState): void {
  qc.setQueryData(qk.linkedinSession, session);
  qc.invalidateQueries({ queryKey: qk.referralQuota });
}

function useLinkedInSessionMutation(fn: () => Promise<LinkedInSessionState> | LinkedInSessionState) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(fn()),
    onSuccess: (session) => applyLinkedInSession(qc, session),
  });
}

export function useDisconnectLinkedIn() {
  return useLinkedInSessionMutation(() => api.disconnectLinkedIn());
}

export function useValidateLinkedIn() {
  return useLinkedInSessionMutation(() => api.validateLinkedIn());
}

export function useResumeLinkedIn() {
  return useLinkedInSessionMutation(() => api.resumeLinkedIn());
}

/** Refresh contact statuses from LinkedIn (FR-NW-15). Pass `true` for the Sync
 *  button (always runs); `false`/omitted is the opportunistic refresh the
 *  Networking surface fires on open, which the sidecar throttles. Replaces the
 *  retired 12 h schedule — no LinkedIn traffic happens without a user present
 *  (`docs/internal/linkedin-addon.md` section 5). */
export function useSyncContacts() {
  return useMutation({
    mutationFn: (force?: boolean) => Promise.resolve(api.syncContacts(Boolean(force))),
    // No invalidation here: a 202 means the sync hasn't touched a contact yet.
    // The SSE terminal handler (contact_sync → invalidateNetworkingLists) does
    // the refetch when the op actually finishes.
  });
}

/** Set the self-imposed LinkedIn rate-limit profile (2026-08-01): membership,
 *  risk%, per-cap override, or reset. Membership/risk changes reset overrides
 *  server-side; the returned session carries the recomputed caps. Invalidates
 *  the referral quota so the popup counter reflects the new caps immediately. */
export function useSetLinkedInRateLimits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      membership_type?: string;
      risk_pct?: number;
      override_key?: string;
      override_value?: number;
      reset_overrides?: boolean;
    }) => Promise.resolve(api.setLinkedInRateLimits(body)),
    onSuccess: (session) => applyLinkedInSession(qc, session),
  });
}

/** The find-referrals popup candidate list for one job (US-NW-09). `enabled`
 *  gates the fetch to when the popup is open for a specific job. */
export function useReferralCandidates(jobId: string | null) {
  return useQuery({
    queryKey: [...qk.referralCandidates, jobId],
    queryFn: () => api.listReferralCandidates(jobId as string),
    enabled: jobId != null,
  });
}

export function useAddContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ContactInput) => Promise.resolve(api.addContact(input)),
    onSuccess: () => invalidateContactLists(qc),
  });
}

export function useUpdateContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<NetContact> & { archived?: boolean } }) =>
      Promise.resolve(api.updateContact(id, patch)),
    onSuccess: () => invalidateContactLists(qc),
  });
}

export function useDiscoverReferrals() {
  const qc = useQueryClient();
  return useMutation({
    // `limit` bumps for the "find 10 more" / Load-more control (FR-NW-02);
    // `confirm` re-runs discovery scoped to the company the user picked in the
    // company-confirm step (after a `needs_company_confirm` event).
    mutationFn: (
      arg:
        | string
        | { jobId: string; limit?: number; page?: number; confirm?: CompanyConfirmPick },
    ) =>
      Promise.resolve(
        typeof arg === "string"
          ? api.discoverReferrals(arg)
          : api.discoverReferrals(arg.jobId, arg.limit ?? 10, arg.confirm, arg.page ?? 1),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.referralCandidates }),
  });
}

/** Grounded LLM rewrite of a contact's referral draft (US-REF-03 Regenerate) —
 *  restored 2026-07-16. Not yet wired to a UI control (the prior repo's
 *  ReferralsModal never called it either — the discover-time draft is the
 *  live path); kept available for a future Regenerate affordance. */
export function useDraftReferral() {
  return useMutation({
    mutationFn: ({ contactId, jobId }: { contactId: string; jobId?: string | null }) =>
      Promise.resolve(api.draftReferral(contactId, jobId)),
  });
}

export function useReachOut() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ReachOutInput) => Promise.resolve(api.reachOut(input)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.referralCandidates });
      qc.invalidateQueries({ queryKey: qk.contacts });
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.referralQuota });
    },
  });
}

// ─── SSE invalidation bridge ─────────────────────────────────────────────────

/** Leading+trailing throttle for invalidation bursts (F-H4). The first call
 *  fires immediately (terminal-state freshness); further calls within
 *  `intervalMs` collapse into ONE trailing call, so the final event of a burst
 *  is never lost — a re-score of 300 jobs invalidates at a bounded rate instead
 *  of once per event. Pure and exported for testability (no frontend unit
 *  runner exists today — see the e2e + typecheck coverage note). */
export interface TrailingThrottle {
  (): void;
  /** Drop any pending trailing call — for effect cleanup, so an unmounted
   *  subscriber's timer can't fire a late invalidation. */
  cancel(): void;
}

export function makeTrailingThrottle(fn: () => void, intervalMs: number): TrailingThrottle {
  let last = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const throttled = () => {
    if (timer != null) return; // a trailing call is already scheduled
    const elapsed = Date.now() - last;
    if (elapsed >= intervalMs) {
      last = Date.now();
      fn();
      return;
    }
    timer = setTimeout(() => {
      timer = null;
      last = Date.now();
      fn();
    }, intervalMs - elapsed);
  };
  throttled.cancel = () => {
    if (timer != null) {
      clearTimeout(timer);
      timer = null;
    }
  };
  return throttled;
}

// Applier events that change the card's Apply slot / run phase. Everything
// else (observed, action_*, screenshot_ready) only feeds the companion panel,
// which has its own scoped subscription (useApplyRun) — invalidating the
// applications list for those was pure churn (F-H4).
const APPLY_PHASE_EVENTS = new Set([
  "apply.phase_changed",
  "apply.blocker_found",
  "apply.ready_for_human",
  "apply.interrupted",
  "apply.completed",
  "apply.waiting_for_packet",
]);

/** Wire the SSE bus (src/api/events.ts) to Query invalidation. Mount once near
 *  the app root. Every group below is throttled (leading + trailing edge):
 *  with `staleTime: 0` each invalidation is a real refetch, and an SSE burst
 *  (re-scoring hundreds of jobs ≈ 3 events per op) used to cancel/restart the
 *  same refetches hundreds of times (F-H4). */
export function useSSEInvalidation(qc: QueryClient): void {
  useEffect(() => {
    const THROTTLE_MS = 300;
    // The Analytics ledger + cost tiles read every operation — keep them live,
    // but at a bounded rate (they fire on EVERY operation event).
    const invalidateLedger = makeTrailingThrottle(() => {
      qc.invalidateQueries({ queryKey: qk.ledger });
      qc.invalidateQueries({ queryKey: qk.costTotals });
    }, THROTTLE_MS);
    // The board is the expensive one — every accumulated page refetches — so
    // give it a wider window.
    const invalidateBoardFeed = makeTrailingThrottle(() => invalidateFeed(qc), 500);
    // Board-level Rescan pill (observed-issue #2): scan/score ops at ANY state
    // (queued/running/terminal) tick the "M of N", so the pill shows "Scanning"
    // promptly on a scheduled scan too — not just after the poll learns it. The
    // /api/scan/progress read is cheap; throttled so a 300-job scoring burst
    // lands ~2 refetches/second, matching the board-feed lane's cadence.
    const invalidateScanProgress = makeTrailingThrottle(
      () => qc.invalidateQueries({ queryKey: qk.scanProgress }),
      500,
    );
    const invalidatePacket = makeTrailingThrottle(() => invalidateTracker(qc), THROTTLE_MS);
    const invalidateNetworkingLists = makeTrailingThrottle(() => {
      invalidateContactLists(qc);
      qc.invalidateQueries({ queryKey: qk.referralQuota });
      qc.invalidateQueries({ queryKey: qk.applications });
    }, THROTTLE_MS);
    const invalidateReferralRoster = makeTrailingThrottle(() => invalidateRoster(qc), THROTTLE_MS);
    // Per-candidate discover events (roster liveness, restored 2026-07-25):
    // same roster-scoped keys the pre-F-H4 bridge invalidated on `candidate`
    // events, but grouped through a wider trailing window so a "Find 10 more"
    // burst lands as ~2 refetches/second, not one per contact.
    const invalidateRosterCandidates = makeTrailingThrottle(() => invalidateRoster(qc), 500);
    const invalidateApplications = makeTrailingThrottle(() => {
      qc.invalidateQueries({ queryKey: qk.applications });
    }, THROTTLE_MS);

    const unsubscribe = eventBus.subscribe((ev) => {
      if (ev.type === "operation") {
        const p = ev.payload;
        invalidateLedger();
        // Only feed-affecting kinds refetch the board, and only at a terminal
        // state (2026-07-11 Save-lag fix): each op bursts queued/running/
        // succeeded events and a naive handler would refetch every loaded
        // page of the infinite board query per event.
        const feedAffecting = p.kind === "scan" || p.kind === "score";
        const terminal = p.state === "succeeded" || p.state === "failed";
        if (feedAffecting && terminal) invalidateBoardFeed();
        // The Rescan pill needs the non-terminal states too (a scan going
        // queued→running is what flips it to "Scanning"); it reads a cheap
        // counts endpoint, so refresh on every scan/score event.
        if (feedAffecting) invalidateScanProgress();
        // Restored: a terminal tailor/cover op flips a card's packet slot
        // (generating → ready/failed) and writes an Activity event — refresh
        // both so the Tracker repaints without a manual reload.
        const packetAffecting = p.kind === "tailor" || p.kind === "cover";
        if (packetAffecting && terminal) invalidatePacket();
        // Restored 2026-07-16: a terminal discover/send/linkedin_login/
        // contact_sync op means the referral roster, the contact kanban, the
        // card's Referrals slot, or the LinkedIn session may have changed —
        // refresh contacts + referral quota + applications so the Tracker/
        // Networking surfaces repaint without a manual reload. `draft` is
        // deliberately excluded — nothing subscribes to its result yet
        // (see useDraftReferral).
        const networkingAffecting =
          p.kind === "discover" ||
          p.kind === "send" ||
          p.kind === "linkedin_login" ||
          p.kind === "contact_sync";
        if (networkingAffecting && terminal) {
          invalidateNetworkingLists();
          if (p.kind === "linkedin_login") {
            qc.invalidateQueries({ queryKey: qk.linkedinSession });
          }
        }
        // A terminal apply op settles the run and the card's Apply slot
        // (applyRunStatus) + writes an Activity event — refresh all three so the
        // Tracker/companion repaint without a manual reload (applier-as-built.md section 8.4).
        // One event per run — no throttle needed.
        if (p.kind === "apply" && terminal) {
          qc.invalidateQueries({ queryKey: qk.applications });
          qc.invalidateQueries({ queryKey: qk.applyRun });
          qc.invalidateQueries({ queryKey: qk.applyRuns });
          qc.invalidateQueries({ queryKey: qk.activity });
        }
      }
      // Applier live-updates (applier-as-built.md section 9.2): only phase-affecting events
      // change the card's Apply slot — the bound run's snapshot is re-read by
      // the companion's own scoped useApplyRun subscription, so the blanket
      // applyRun invalidation that doubled it up is gone (F-H4).
      if (ev.type === "apply" && APPLY_PHASE_EVENTS.has(ev.payload.event)) {
        invalidateApplications();
      }
      // Networking live-updates (Track N3): discover/send progress for the
      // popup + kanban (US-NW-09) — the popup's own SSE subscription (in
      // ReferralsModal) reads company-confirm / per-contact send outcomes off
      // this same event; here we just keep the cached lists honest. Per-contact
      // `candidate` events grow the roster LIVE through their own wider
      // throttle group (they burst once per found contact); everything else —
      // including the `discovered` summary that closes every discover pass,
      // the final-consistency backstop — keeps the tighter group (F-H4).
      if (ev.type === "networker") {
        if (ev.payload.phase === "candidate") invalidateRosterCandidates();
        else invalidateReferralRoster();
      }
      // LinkedIn session capture (N4): connecting → connected/disconnected
      // repaints the Networking pill (and, once built, the Settings status chip).
      if (ev.type === "linkedin") {
        qc.invalidateQueries({ queryKey: qk.linkedinSession });
        qc.invalidateQueries({ queryKey: qk.referralQuota });
      }
    });

    // Outage recovery (F-M5): events missed while disconnected are never
    // replayed (events.ts header contract), so a reconnecting → live
    // transition re-reads EVERYTHING from the API in one blanket invalidation.
    let prevState: StreamState | null = null;
    const unsubscribeState = eventBus.subscribe(null, (state) => {
      if (state === "live" && prevState === "reconnecting") {
        void qc.invalidateQueries();
      }
      prevState = state;
    });

    return () => {
      unsubscribe();
      unsubscribeState();
      // Drop pending trailing timers — a late invalidation after unmount
      // would refetch on behalf of a subscriber that no longer exists.
      invalidateLedger.cancel();
      invalidateBoardFeed.cancel();
      invalidateScanProgress.cancel();
      invalidatePacket.cancel();
      invalidateNetworkingLists.cancel();
      invalidateReferralRoster.cancel();
      invalidateRosterCandidates.cancel();
      invalidateApplications.cancel();
    };
  }, [qc]);
}
