// The RealApi methods for the board, a job, and adding a job (F-P3).
// Each surface extends the one before it, so RealApi stays 1 class,
// and `real.ts` ends the chain.

import { apiFetch } from "../client";
import {
  type Application,
  type AttachLinkInput,
  type AttachLinkResult,
  type BoardFilters,
  type BoardPage,
  type DuplicatePrompt,
  type ExplainCost,
  type Job,
  type JobDraft,
  type JobFactors,
  JobTombstonedError,
  type ManualApplicationInput,
  type PlaceSuggestion,
  type PromptTestInput,
  type ScanProgress,
  type TombstoneResult,
} from "../types";
import {
  appFromDto,
  type ApplicationDTO,
  type AttachLinkResultDTO,
  type BoardPageDTO,
  type JobDTO,
  type JobPreviewDTO,
  type ScanProgressDTO,
  STAGE_TO_COLUMN,
  toJob,
  toLinkCandidate,
} from "./mappers";
import { ApiError, Transport } from "./transport";

export class JobsApi extends Transport {
  // ── jobs ───────────────────────────────────────────────────────────────
  // Restored: `/api/applications` now exists — cross-reference it so
  // `saved` reflects a real tracked card instead of always reporting false.
  async listJobs(): Promise<Job[]> {
    const [jobs, apps] = await Promise.all([
      this.req<JobDTO[]>("/api/jobs"),
      this.req<ApplicationDTO[]>("/api/applications"),
    ]);
    const saved = new Set(apps.map((a) => a.job_id));
    return jobs.map((d) => toJob(d, saved.has(d.id)));
  }

  /** One page of the board feed + header meta (FR-JB-02/10): saved-excluded,
   *  Expired-inclusive (greyed), paginated server-side (no silent 200-row cap).
   *  `listQ`/`textQ` (FR-JB-13) are server-side search filters applied before
   *  pagination — shallow (title/company/location) vs deep (JD + score texts). */
  async getBoard(
    page = 0,
    listQ = "",
    textQ = "",
    link = "",
    filters: BoardFilters = {},
  ): Promise<BoardPage> {
    const params = new URLSearchParams({ page: String(page) });
    if (listQ) params.set("list_q", listQ);
    if (textQ) params.set("text_q", textQ);
    // Every filter and the sort run server-side, before pagination: a pass
    // over the loaded pages would miss matches on unloaded ones (finding 6).
    if (link) params.set("link", link);
    for (const [k, v] of Object.entries(filters)) {
      if (v) params.set(k, String(v));
    }
    const d = await this.req<BoardPageDTO>(`/api/board?${params.toString()}`);
    return {
      jobs: (d.jobs ?? []).map((j) => toJob(j, false)),
      total: d.total,
      page: d.page,
      page_size: d.pageSize,
      scan_status: d.scanStatus as BoardPage["scan_status"],
      last_scan_at: d.lastScanAt ?? null,
      scan_error: d.scanError ?? null,
    };
  }

  /** Board-level scan + scoring progress (observed-issue #2) — the Rescan status
   *  pill's data. Derived server-side from the ledger; cheap enough to poll while
   *  a scan/scoring cycle is live (the query gates its own polling to that window). */
  async getScanProgress(): Promise<ScanProgress> {
    const d = await this.req<ScanProgressDTO>("/api/scan/progress");
    return {
      scan_running: d.scan_running,
      last_scan_at: d.last_scan_at ?? null,
      new_found: d.new_found,
      score_pending: d.score_pending,
      score_done: d.score_done,
    };
  }

  /** Trashed jobs (US-JB-11) — the Trash modal's own source, kept off the board. */
  async listTrash(): Promise<Job[]> {
    const jobs = await this.req<JobDTO[]>("/api/jobs?feed_state=removed");
    return jobs.map((d) => toJob(d, false));
  }

  async getJob(id: string): Promise<Job | undefined> {
    try {
      return toJob(await this.req<JobDTO>(`/api/jobs/${id}`), false);
    } catch (e) {
      // Only a true 404 means "no such job". Anything else (500, auth,
      // network) must propagate — masking it as undefined made a transient
      // failure render as "(job removed)" (2026-07-24 graceful-failure audit).
      if (e instanceof ApiError && e.status === 404) return undefined;
      throw e;
    }
  }

  // Add a job posting, step 1 (US-JB-07): fetch + extract editable fields (no persist).
  async previewJob(url: string): Promise<JobDraft> {
    const d = (await this.json("POST", "/api/jobs/preview", { url }).catch((e: unknown) => {
      // 409 = the URL was permanently deleted (tombstoned) — final, not editable.
      if (e instanceof ApiError && e.status === 409) throw new JobTombstonedError();
      throw e;
    })) as JobPreviewDTO;
    return {
      canonical_url: d.canonical_url,
      title: d.title,
      company: d.company,
      location: d.location,
      description: d.description,
      salary: d.salary ?? "",
      source_adapter: d.source_adapter,
    };
  }

  // Add a job posting, step 2: persist the (edited) draft. The sidecar dedups against
  // active URLs, restores a Trashed one, and refuses a tombstoned one (2026-07-09).
  async addJobByUrl(draft: JobDraft): Promise<Job> {
    const d = (await this.json("POST", "/api/jobs", {
      canonical_url: draft.canonical_url,
      title: draft.title,
      company: draft.company,
      location: draft.location,
      description: draft.description,
      salary: draft.salary,
      source_adapter: draft.source_adapter || "paste-url",
    }).catch((e: unknown) => {
      if (e instanceof ApiError && e.status === 409) throw new JobTombstonedError();
      throw e;
    })) as JobDTO;
    return toJob(d, false);
  }

  /** Promotion (url-less-jobs.md section 5.4): attach a posting link to a job
   *  that has none. The sidecar probes the URL and runs the compound check
   *  before it writes, so a non-`attached` status is a question for the user,
   *  not an error. */
  async attachJobLink(input: AttachLinkInput): Promise<AttachLinkResult> {
    const d = (await this.json("POST", `/api/jobs/${input.job_id}/link`, {
      url: input.url,
      confirm: input.confirm ?? false,
    })) as AttachLinkResultDTO;
    return {
      status: d.status,
      job: d.job ? toJob(d.job, false) : null,
      pasted: d.pasted ? toLinkCandidate(d.pasted) : null,
      card: d.card ? toLinkCandidate(d.card) : null,
      other_job_id: d.other_job_id ?? null,
    };
  }

  /** Triage (url-less-jobs.md section 9.1.1): score a JD with nothing saved.
   *  A score op with an inline `job` and no `job_id` writes no rows — the
   *  number and its reasons come back in the operation's own `result_ref`.
   *  Returns the op id; the caller polls `getOperation` for the result. */
  async scoreJobText(description: string): Promise<string> {
    const d = (await this.json("POST", "/api/operations/score", {
      job: description,
    })) as { id: string };
    return d.id;
  }

  /** "Add a job application" (FR-TR manual-add): log a job applied to outside
   *  the app. Multipart so the user's resume/cover files ride along; the sidecar
   *  upserts the job, creates an `origin=manual` card, and stores the docs
   *  content-addressed. Surfaces the sidecar's verbatim detail on 409/422. */
  async createManualApplication(input: ManualApplicationInput): Promise<Application> {
    const form = new FormData();
    form.append("canonical_url", input.canonical_url);
    form.append("title", input.title);
    form.append("company", input.company);
    form.append("location", input.location);
    form.append("description", input.description);
    form.append("salary", input.salary);
    form.append("source_adapter", input.source_adapter || "paste-url");
    form.append("column", STAGE_TO_COLUMN[input.stage]);
    form.append("notes_markdown", input.notes);
    if (input.resume) form.append("resume", input.resume);
    if (input.cover) form.append("cover", input.cover);
    return appFromDto(
      await this.postForm<ApplicationDTO>("/api/applications/manual", form, "add application"),
    );
  }

  /** Attach a resume/cover FILE to an existing application — the Upload button
   *  on the Tailored resume / Cover letter editors. Multipart; replaces any
   *  prior file of that kind. Surfaces the sidecar's verbatim detail on 422. */
  async attachDocument(
    applicationId: string,
    kind: "tailored_resume" | "cover_letter",
    file: File,
  ): Promise<Application> {
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    return appFromDto(
      await this.postForm<ApplicationDTO>(
        `/api/applications/${applicationId}/documents`,
        form,
        "attach document",
      ),
    );
  }

  /** Detach the (application, kind) resume/cover file — the ✕ on the chip. */
  async removeDocument(
    applicationId: string,
    kind: "tailored_resume" | "cover_letter",
  ): Promise<Application> {
    return appFromDto(
      await this.req<ApplicationDTO>(`/api/applications/${applicationId}/documents/${kind}`, {
        method: "DELETE",
      }),
    );
  }

  /** One attached document as a Blob (authed — a plain href can't carry the
   *  bearer token). The caller wraps it in an object URL to trigger a download. */
  async fetchDocument(documentId: string): Promise<Blob> {
    const info = await this.info();
    const res = await apiFetch(info, `/api/documents/${documentId}`);
    if (!res.ok) throw new ApiError(res.status, `GET document ${documentId} → ${res.status}`);
    return res.blob();
  }

  // Empty Trash (US-JB-11 / FR-SYS-04): tombstone + remove every Trashed job.
  async emptyTrash(): Promise<TombstoneResult> {
    return (await this.json("POST", "/api/jobs/trash/empty", {})) as TombstoneResult;
  }

  // Delete forever (US-JB-11): tombstone + remove one Trashed job.
  async tombstoneJob(id: string): Promise<TombstoneResult> {
    return (await this.json("POST", `/api/jobs/${id}/tombstone`, {})) as TombstoneResult;
  }

  // Save posts an application row; un-save finds it by job_id and deletes it.
  // Per-job toggles (US-TL-03 / US-JB-03); omitted means the auto-packet setting.
  // `generate_prep` is accepted but dropped: this sidecar has no save-time prep.
  async setJobSaved(
    id: string,
    saved: boolean,
    opts?: {
      generate_resume?: boolean | null;
      generate_cover?: boolean | null;
      generate_prep?: boolean | null;
    },
  ): Promise<Job | undefined> {
    if (saved) {
      await this.json("POST", "/api/applications", {
        job_id: id,
        ...(opts?.generate_resume != null && { generate_resume: opts.generate_resume }),
        ...(opts?.generate_cover != null && { generate_cover: opts.generate_cover }),
      });
    } else {
      const apps = await this.req<ApplicationDTO[]>("/api/applications");
      const app = apps.find((a) => a.job_id === id);
      if (app) await this.req(`/api/applications/${app.id}`, { method: "DELETE" });
    }
    return this.getJob(id);
  }

  /** Run a prompt draft once without committing it. Returns the operation id;
   *  the caller polls `getOperation` for the output, exactly like triage. */
  async testPrompt(kind: string, input: PromptTestInput): Promise<string> {
    const d = (await this.json(
      "POST",
      `/api/settings/prompts/${kind}/test`,
      input as unknown as Record<string, unknown>,
    )) as { id: string };
    return d.id;
  }

  /** The location picker's suggestions (place-picker.md section 3). */
  async suggestPlaces(q: string): Promise<PlaceSuggestion[]> {
    return this.req<PlaceSuggestion[]>(`/api/geo/suggest?q=${encodeURIComponent(q)}`);
  }

  /** The duplicate this job probably is, or null (url-less-jobs.md 9.6). */
  async getJobDuplicate(id: string): Promise<DuplicatePrompt | null> {
    return this.req<DuplicatePrompt | null>(`/api/jobs/${id}/duplicate`);
  }

  async dismissJobDuplicate(id: string, otherJobId: string): Promise<void> {
    await this.json("POST", `/api/jobs/${id}/duplicate/dismiss`, {
      other_job_id: otherJobId,
    });
  }

  /** What an AI score will run on, and what it has cost this user before. */
  async getExplainCost(): Promise<ExplainCost> {
    return this.req<ExplainCost>("/api/scoring/explain-cost");
  }

  /** 1 click, 1 job, 1 AI call. An ordinary score operation, so cost, the cap,
   *  Stop and Retry all apply. */
  async explainJob(id: string): Promise<{ id: string }> {
    return this.json("POST", `/api/jobs/${id}/explain`, {}) as Promise<{ id: string }>;
  }

  /** The bars behind a job's number. Read-only and free — every method stores
   *  its axes when it scores, so nothing here costs a model call. */
  async getJobFactors(id: string): Promise<JobFactors> {
    return this.req<JobFactors>(`/api/jobs/${id}/factors`);
  }

  async setJobBoardState(id: string, board_state: Job["board_state"]): Promise<Job | undefined> {
    const feed_state = board_state === "trashed" ? "removed" : "active";
    const d = (await this.json("PATCH", `/api/jobs/${id}`, { feed_state })) as JobDTO;
    return toJob(d, false);
  }
}
