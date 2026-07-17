// TanStack Query hooks over the (sidecar-backed) client + SSE invalidation.
// Server state lives in Query; the SSE bus invalidates the relevant keys so
// feed deltas / operation progress flow into the UI (architecture §6:
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

import { eventBus } from "./events";
import { api } from "./index";
import type {
  Application,
  BoardPage,
  CompanyConfirmPick,
  ContactInput,
  EngineSaveInput,
  Job,
  JobDraft,
  LinkedInSessionState,
  NetContact,
  Priority,
  ReachOutInput,
  Settings,
  Stage,
} from "./types";

export const qk = {
  jobs: ["jobs"] as const,
  board: ["board"] as const,
  trash: ["trash"] as const,
  applications: ["applications"] as const,
  activity: ["activity"] as const,
  networking: ["networking"] as const,
  archived: ["archived"] as const,
  profile: ["profile"] as const,
  onboarding: ["onboarding"] as const,
  settings: ["settings"] as const,
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
/** The operations ledger — the Analytics table + cost source of truth (§10). */
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

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Settings>) => Promise.resolve(api.updateSettings(patch)),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings }),
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

// ─── Apply Runs (the agentic Applier — applier.md §8/§9) ─────────────────────

/** All Apply Runs for one application (§8.3 — the immutable attempt history). */
export function useApplyRuns(applicationId: string | null) {
  return useQuery({
    queryKey: [...qk.applyRuns, applicationId],
    queryFn: () => api.listApplyRuns(applicationId as string),
    enabled: applicationId != null,
  });
}

/** One Apply Run's live snapshot for the companion panel. Poll-light: the run
 *  is refetched only when an `apply` SSE event for THIS run_id lands, or a
 *  terminal apply operation fires — never on a timer (§9.2). Seeds/keeps the
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
        const p = ev.payload as { run_id?: string };
        if (p.run_id === runId) {
          qc.invalidateQueries({ queryKey: [...qk.applyRun, runId] });
        }
        return;
      }
      // A terminal apply operation is the authoritative end-of-run signal — the
      // run row is settled by then, so re-read its final snapshot.
      if (ev.type === "operation") {
        const p = ev.payload as { kind?: string; state?: string };
        if (p.kind === "apply" && (p.state === "succeeded" || p.state === "failed")) {
          qc.invalidateQueries({ queryKey: [...qk.applyRun, runId] });
        }
      }
    });
  }, [runId, qc]);
  return query;
}

/** Start an Apply Run (§8.1) — no pre-confirm; the click IS the action.
 *  `retryOfRunId` starts a fresh run linked to the prior one (§8.3). Seeds the
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

/** Cooperative cancel (§8.2) — lands the run as `interrupted`. */
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

/** The human's post-handoff attestation (§8.4). A `true` advances the card to
 *  Applied — refresh applications + the Activity tab. */
export function useAttestApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, submitted }: { runId: string; submitted: boolean }) =>
      Promise.resolve(api.attestApplyRun(runId, submitted)),
    onSuccess: (run) => {
      qc.setQueryData([...qk.applyRun, run.id], run);
      qc.invalidateQueries({ queryKey: qk.applications });
      qc.invalidateQueries({ queryKey: qk.activity });
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

function useLinkedInSessionMutation(fn: () => Promise<LinkedInSessionState> | LinkedInSessionState) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Promise.resolve(fn()),
    onSuccess: (session) => {
      qc.setQueryData(qk.linkedinSession, session);
      qc.invalidateQueries({ queryKey: qk.referralQuota });
    },
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

export function useSetLinkedInTier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tier: "new" | "seasoned") => Promise.resolve(api.setLinkedInTier(tier)),
    onSuccess: (session) => {
      qc.setQueryData(qk.linkedinSession, session);
      qc.invalidateQueries({ queryKey: qk.referralQuota });
    },
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.contacts });
      qc.invalidateQueries({ queryKey: qk.archivedContacts });
    },
  });
}

export function useUpdateContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<NetContact> & { archived?: boolean } }) =>
      Promise.resolve(api.updateContact(id, patch)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.contacts });
      qc.invalidateQueries({ queryKey: qk.archivedContacts });
    },
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

/** Wire the SSE bus (src/api/events.ts) to Query invalidation. Mount once near
 *  the app root. */
export function useSSEInvalidation(qc: QueryClient): void {
  useEffect(() => {
    return eventBus.subscribe((ev) => {
      if (ev.type === "operation") {
        // The Analytics ledger + cost tiles read every operation, so keep them
        // live on any operation event (cheap queries; the surface is often open).
        qc.invalidateQueries({ queryKey: qk.ledger });
        qc.invalidateQueries({ queryKey: qk.costTotals });
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
          qc.invalidateQueries({ queryKey: qk.contacts });
          qc.invalidateQueries({ queryKey: qk.archivedContacts });
          qc.invalidateQueries({ queryKey: qk.referralQuota });
          qc.invalidateQueries({ queryKey: qk.applications });
          if (p.kind === "linkedin_login") {
            qc.invalidateQueries({ queryKey: qk.linkedinSession });
          }
        }
        // A terminal apply op settles the run and the card's Apply slot
        // (applyRunStatus) + writes an Activity event — refresh all three so the
        // Tracker/companion repaint without a manual reload (applier.md §8.4).
        if (p.kind === "apply" && terminal) {
          qc.invalidateQueries({ queryKey: qk.applications });
          qc.invalidateQueries({ queryKey: qk.applyRun });
          qc.invalidateQueries({ queryKey: qk.applyRuns });
          qc.invalidateQueries({ queryKey: qk.activity });
        }
      }
      // Applier live-updates (applier.md §9.2): a phase/observe/screenshot/
      // blocker event may change the card's Apply slot and the bound run. The
      // companion's own useApplyRun subscription re-reads the run snapshot; here
      // we keep the card's applyRunStatus honest as the run advances.
      if (ev.type === "apply") {
        qc.invalidateQueries({ queryKey: qk.applications });
        qc.invalidateQueries({ queryKey: qk.applyRun });
      }
      // Networking live-updates (Track N3): discover/send progress for the
      // popup + kanban (US-NW-09) — the popup's own SSE subscription (in
      // ReferralsModal) reads company-confirm / per-contact send outcomes off
      // this same event; here we just keep the cached lists honest.
      if (ev.type === "networker") {
        qc.invalidateQueries({ queryKey: qk.referralCandidates });
        qc.invalidateQueries({ queryKey: qk.contacts });
        qc.invalidateQueries({ queryKey: qk.applications });
      }
      // LinkedIn session capture (N4): connecting → connected/disconnected
      // repaints the Networking pill (and, once built, the Settings status chip).
      if (ev.type === "linkedin") {
        qc.invalidateQueries({ queryKey: qk.linkedinSession });
        qc.invalidateQueries({ queryKey: qk.referralQuota });
      }
    });
  }, [qc]);
}
