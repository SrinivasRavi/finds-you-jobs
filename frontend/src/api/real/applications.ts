// The RealApi methods for the tracker, the packet, and apply runs (F-P3).
// Each surface extends the one before it, so RealApi stays 1 class,
// and `real.ts` ends the chain.

import { apiFetch } from "../client";
import type {
  ActivityEntry,
  Application,
  ApplyRun,
  NetworkingContact,
  PacketState,
  Priority,
  Stage,
} from "../types";
import {
  type ActivityEntryDTO,
  appFromDto,
  type ApplicationDTO,
  type ApplyRunDTO,
  type NetworkingContactDTO,
  placeholderJob,
  STAGE_TO_COLUMN,
  toApplication,
  toApplyRun,
  toJob,
} from "./mappers";
import { ApiError } from "./transport";
import { JobsApi } from "./jobs";

export class ApplicationsApi extends JobsApi {
  // ── applications ─────────────────────────────────────────────────────────
  // Restored from the prior repo's real.ts, trimmed to the live endpoints (no
  // /networking, /apply, /prep — those surfaces haven't landed here). The job
  // rides embedded on each ApplicationDTO (server-side join) — never joined
  // against a capped /api/jobs list (the "(job removed)" bug).
  async listApplications(): Promise<Application[]> {
    const apps = await this.req<ApplicationDTO[]>("/api/applications");
    return apps.map(appFromDto);
  }

  async getApplication(id: string): Promise<Application | undefined> {
    try {
      return appFromDto(await this.req<ApplicationDTO>(`/api/applications/${id}`));
    } catch (e) {
      // 404 → genuinely gone; every other failure propagates instead of
      // masquerading as "not found" (2026-07-24 graceful-failure audit).
      if (e instanceof ApiError && e.status === 404) return undefined;
      throw e;
    }
  }

  /** Real Activity log for one application (US-TR-03 / FR-TR-03) — composed
   *  server-side from the ledger + card events, never synthesized. */
  async getApplicationActivity(id: string): Promise<ActivityEntry[]> {
    const rows = await this.req<ActivityEntryDTO[]>(`/api/applications/${id}/activity`);
    return rows.map((r) => ({
      kind: r.kind as ActivityEntry["kind"],
      label: r.label,
      state: r.state ?? null,
      at: r.at ?? null,
    }));
  }

  /** The role's referral contacts + statuses — detail-modal Networking tab
   *  (US-TR-03), restored 2026-07-16. */
  async getApplicationNetworking(id: string): Promise<NetworkingContact[]> {
    const rows = await this.req<NetworkingContactDTO[]>(`/api/applications/${id}/networking`);
    return rows.map((r) => ({
      contact_id: r.contact_id,
      name: r.name,
      role: r.role,
      company: r.company,
      linkedin_url: r.linkedin_url,
      connection_status: r.connection_status,
      audience_tag: r.audience_tag,
      last_message: r.last_message ?? null,
      last_message_at: r.last_message_at ?? null,
      last_outcome: r.last_outcome ?? null,
    }));
  }

  async listArchived(): Promise<Application[]> {
    const apps = await this.req<ApplicationDTO[]>("/api/applications?include_archived=true");
    return apps.filter((d) => d.archived_at != null).map(appFromDto);
  }

  /** The two PATCH paths (card patch, artifact patch) re-read the job by id
   *  rather than trusting the response's embedded `job` — kept as-is, so the
   *  placeholder here covers a job that has since gone, not an absent join. */
  private async appFromPatchedDto(d: ApplicationDTO): Promise<Application> {
    const job = await this.getJob(d.job_id);
    return toApplication(d, job ?? toJob(placeholderJob(d.job_id), true));
  }

  private async patchApp(id: string, body: unknown): Promise<Application | undefined> {
    return this.appFromPatchedDto(
      (await this.json("PATCH", `/api/applications/${id}`, body)) as ApplicationDTO,
    );
  }

  async updateApplication(id: string, patch: Partial<Application>): Promise<Application | undefined> {
    const body: Record<string, unknown> = {};
    if (patch.stage !== undefined) body.column = STAGE_TO_COLUMN[patch.stage];
    if (patch.priority !== undefined) body.priority = patch.priority;
    if (patch.notes !== undefined) body.notes_markdown = patch.notes;
    if (patch.intent !== undefined) body.intent = patch.intent;
    return this.patchApp(id, body);
  }

  moveApplication(id: string, stage: Stage): Promise<Application | undefined> {
    return this.patchApp(id, { column: STAGE_TO_COLUMN[stage] });
  }

  setPriority(id: string, priority: Priority): Promise<Application | undefined> {
    return this.patchApp(id, { priority });
  }

  async archiveApplication(id: string): Promise<void> {
    await this.json("PATCH", `/api/applications/${id}`, { archived: true });
  }

  async unarchiveApplication(id: string): Promise<void> {
    await this.json("PATCH", `/api/applications/${id}`, { archived: false });
  }

  async returnToBoard(id: string): Promise<void> {
    await this.req(`/api/applications/${id}`, { method: "DELETE" });
  }

  /** Permanently delete an archived card (Deleted Applications modal). Same
   *  endpoint as returnToBoard — the card and its children go, the underlying
   *  job survives and may resurface in Discover (mirrors the FR-SYS-06
   *  retention purge semantics). */
  async deleteApplicationForever(id: string): Promise<void> {
    await this.req(`/api/applications/${id}`, { method: "DELETE" });
  }

  /** Manual/regenerate packet build (US-TL-02) — per-artifact: the tailor and
   *  cover modules are independent. `guidance` (FR-TL-02) reaches the Tailorer
   *  and is persisted with the variant. */
  async generatePacket(
    appId: string,
    kinds: { resume: boolean; cover: boolean } = { resume: true, cover: true },
    guidance = "",
  ): Promise<void> {
    await this.json("POST", `/api/applications/${appId}/packet`, { ...kinds, guidance });
  }

  /** Persist an edited variant + the Approve-and-Save flip (US-RES-02 / FR-RES-02).
   *  `kind` maps the popup kind to the artifact kind. */
  async patchArtifact(
    appId: string,
    kind: "tailored" | "cover",
    patch: { markdown?: string; approved?: boolean },
  ): Promise<Application | undefined> {
    const artifactKind = kind === "cover" ? "cover_letter" : "tailored_resume";
    return this.appFromPatchedDto(
      (await this.json(
        "PATCH", `/api/applications/${appId}/artifacts/${artifactKind}`, patch,
      )) as ApplicationDTO,
    );
  }

  async packetState(appId: string): Promise<PacketState> {
    const d = await this.req<ApplicationDTO>(`/api/applications/${appId}`);
    return (d.packetState as PacketState) ?? "none";
  }

  // ── apply runs (the agentic Applier — applier-as-built.md section 8/section 9) ───────────────────
  // Starting Apply IS the action (section 8.1): no pre-confirm modal, the run is
  // created and the op enqueued immediately. `retryOfRunId` links a Retry /
  // Reopen-and-refill to the immutable prior run (section 8.3).
  async startApply(applicationId: string, retryOfRunId?: string): Promise<ApplyRun> {
    const body = retryOfRunId ? { retry_of_run_id: retryOfRunId } : {};
    const d = (await this.json(
      "POST", `/api/applications/${applicationId}/apply`, body,
    )) as ApplyRunDTO;
    return toApplyRun(d);
  }

  async listApplyRuns(applicationId: string): Promise<ApplyRun[]> {
    const rows = await this.req<ApplyRunDTO[]>(`/api/applications/${applicationId}/apply-runs`);
    return rows.map(toApplyRun);
  }

  /** The run snapshot — a reopened companion reads this instead of depending on
   *  having seen every prior SSE event (section 9.2). */
  async getApplyRun(runId: string): Promise<ApplyRun> {
    return toApplyRun(await this.req<ApplyRunDTO>(`/api/apply-runs/${runId}`));
  }

  /** Cooperative cancel (section 8.2) — the loop lands the run as `interrupted`. */
  async cancelApplyRun(runId: string): Promise<ApplyRun> {
    return toApplyRun((await this.json("POST", `/api/apply-runs/${runId}/cancel`, {})) as ApplyRunDTO);
  }

  /** The human's word after the P1 handoff (section 8.4): `true` records a user-attested
   *  submission and advances the card to Applied; `false` leaves it in place. */
  /** Ask the run's own live browser to click Submit once (S-A5). 409 when the
   *  review window is over, because the page it needs is gone. */
  async submitApplyRun(runId: string): Promise<ApplyRun> {
    return toApplyRun(
      (await this.json("POST", `/api/apply-runs/${runId}/submit`, {})) as ApplyRunDTO,
    );
  }

  /** Bring the run's tab to the front of the Applier window (applier/window.md
   *  section 4.5). `false` once the tab is gone. */
  async showApplyRun(runId: string): Promise<boolean> {
    const d = (await this.json("POST", `/api/apply-runs/${runId}/show`, {})) as { shown: boolean };
    return d.shown;
  }

  async attestApplyRun(runId: string, submitted: boolean): Promise<ApplyRun> {
    return toApplyRun(
      (await this.json("POST", `/api/apply-runs/${runId}/attest`, { submitted })) as ApplyRunDTO,
    );
  }

  /** One evidence PNG as a Blob — the caller wraps it in an object URL for
   *  `<img>` (SSE can't set the Authorization header, so this authed fetch is
   *  the only way to load it). */
  async fetchApplyScreenshot(runId: string, index: number): Promise<Blob> {
    const info = await this.info();
    const res = await apiFetch(info, `/api/apply-runs/${runId}/screenshots/${index}`);
    if (!res.ok) {
      throw new ApiError(res.status, `GET screenshot ${runId}/${index} → ${res.status}`);
    }
    return res.blob();
  }
}
