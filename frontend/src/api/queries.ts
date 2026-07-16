// TanStack Query hooks over the (sidecar-backed) client + SSE invalidation.
// Server state lives in Query; the SSE bus invalidates the relevant keys so
// feed deltas / operation progress flow into the UI (architecture §6:
// "TanStack Query, invalidated by SSE events").
//
// Job Board / Dev status page / main.tsx guard hooks, plus the applications/
// tracker hooks (restored — `/api/applications` now exists). Networking/apply/
// prep/packet-prompts/spans/linkedin hooks return with their own commits, once
// the sidecar grows that surface.

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect } from "react";

import { eventBus } from "./events";
import { api } from "./index";
import type { Application, BoardPage, Job, JobDraft, Priority, Stage } from "./types";

export const qk = {
  jobs: ["jobs"] as const,
  board: ["board"] as const,
  trash: ["trash"] as const,
  applications: ["applications"] as const,
  activity: ["activity"] as const,
  archived: ["archived"] as const,
  profile: ["profile"] as const,
  onboarding: ["onboarding"] as const,
  settings: ["settings"] as const,
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
export function useProfile() {
  return useQuery({ queryKey: qk.profile, queryFn: () => api.getProfile() });
}
/** First-launch guard (FR-OB-01): whether a MasterProfile exists ⟺ onboarded. */
export function useMasterProfileExists() {
  return useQuery({
    queryKey: qk.onboarding,
    queryFn: () => Promise.resolve(api.hasMasterProfile()),
  });
}
export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: () => api.getSettings() });
}

// ─── Mutations ───────────────────────────────────────────────────────────────

/** Invalidate every board-feed view (list, paginated board, trash) at once. */
function invalidateFeed(qc: QueryClient): void {
  qc.invalidateQueries({ queryKey: qk.jobs });
  qc.invalidateQueries({ queryKey: qk.board });
  qc.invalidateQueries({ queryKey: qk.trash });
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

/** Add-by-URL step 1: fetch the pasted URL → editable draft (no persist). */
export function useJobPreview() {
  return useMutation({
    mutationFn: (url: string): Promise<JobDraft> => Promise.resolve(api.previewJob(url)),
  });
}

/** Add-by-URL step 2: persist the (edited) draft. */
export function useAddJobByUrl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (draft: JobDraft): Promise<Job> => Promise.resolve(api.addJobByUrl(draft)),
    onSuccess: () => invalidateFeed(qc),
  });
}

/** Fire an on-demand scan (zero-LLM). The board refreshes as jobs land. */
export function useTriggerScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(api.enqueueOperation("scan")),
    onSuccess: () => invalidateFeed(qc),
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (master_md: string) => Promise.resolve(api.updateProfile(master_md)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.profile }),
  });
}

// ─── Applications / Tracker mutations (restored) ─────────────────────────────

export function useMoveApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, stage }: { id: string; stage: Stage }) =>
      Promise.resolve(api.moveApplication(id, stage)),
    // A move writes an Activity event (FR-TR-03) → refresh the detail-modal tab.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.activity });
    },
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.activity });
    },
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

// ─── SSE invalidation bridge ─────────────────────────────────────────────────

/** Wire the SSE bus (src/api/events.ts) to Query invalidation. Mount once near
 *  the app root. */
export function useSSEInvalidation(qc: QueryClient): void {
  useEffect(() => {
    return eventBus.subscribe((ev) => {
      if (ev.type === "operation") {
        // Only feed-affecting kinds refetch the board, and only at a terminal
        // state (2026-07-11 Save-lag fix): each op bursts queued/running/
        // succeeded events and a naive handler would refetch every loaded
        // page of the infinite board query per event.
        const p = ev.payload as { kind?: string; state?: string };
        const feedAffecting = p.kind === "scan" || p.kind === "score";
        const terminal = p.state === "succeeded" || p.state === "failed";
        if (feedAffecting && terminal) invalidateFeed(qc);
        // Restored: a terminal tailor/cover op flips a card's packet slot
        // (generating → ready/failed) and writes an Activity event — refresh
        // both so the Tracker repaints without a manual reload.
        const packetAffecting = p.kind === "tailor" || p.kind === "cover";
        if (packetAffecting && terminal) {
          qc.invalidateQueries({ queryKey: qk.applications });
          qc.invalidateQueries({ queryKey: qk.activity });
        }
      }
    });
  }, [qc]);
}
