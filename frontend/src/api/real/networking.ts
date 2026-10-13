// The RealApi methods for contacts, referrals, the LinkedIn session (F-P3).
// Each surface extends the one before it, so RealApi stays 1 class,
// and `real.ts` ends the chain.

import type {
  CompanyConfirmPick,
  ContactInput,
  ContactSyncResult,
  LinkedInSessionState,
  NetContact,
  ReachOutInput,
  ReachOutResult,
  ReferralCandidates,
  ReferralQuota,
} from "../types";
import {
  type ContactDTO,
  type LinkedInSessionDTO,
  type QuotaDTO,
  type ReferralCandidatesDTO,
  toCandidate,
  toContact,
  toLinkedInSession,
} from "./mappers";
import { OperationsApi } from "./operations";

export class NetworkingApi extends OperationsApi {
  // ── networking (Track N3) — restored 2026-07-16 from the prior repo's
  // real.ts: the referral-outreach backend now exists. ────────────────────
  async listContacts(company?: string): Promise<NetContact[]> {
    const q = company ? `?company=${encodeURIComponent(company)}` : "";
    const rows = await this.req<ContactDTO[]>(`/api/contacts${q}`);
    return rows.map(toContact);
  }

  /** The "Deleted Contacts" recovery roster — archived rows only (US-NW-02). */
  async listArchivedContacts(): Promise<NetContact[]> {
    const rows = await this.req<ContactDTO[]>("/api/contacts?archived=true");
    return rows.map(toContact);
  }

  async addContact(input: ContactInput): Promise<NetContact> {
    const d = (await this.json("POST", "/api/contacts", {
      linkedin_url: input.linkedin_url,
      name: input.name ?? "",
      current_company: input.current_company ?? "",
      current_role: input.current_role ?? "",
      connection_status: input.connection_status ?? "sent",
      audience_tag: input.audience_tag ?? "other",
    })) as ContactDTO;
    return toContact(d);
  }

  async updateContact(id: string, patch: Partial<NetContact> & { archived?: boolean }): Promise<NetContact> {
    const body: Record<string, unknown> = {};
    if (patch.connection_status !== undefined) body.connection_status = patch.connection_status;
    if (patch.audience_tag !== undefined) body.audience_tag = patch.audience_tag;
    if (patch.archived !== undefined) body.archived = patch.archived;
    const d = (await this.json("PATCH", `/api/contacts/${id}`, body)) as ContactDTO;
    return toContact(d);
  }

  async listReferralCandidates(jobId: string): Promise<ReferralCandidates> {
    const d = await this.req<ReferralCandidatesDTO>(`/api/jobs/${jobId}/referrals/candidates`);
    return {
      job_id: d.job_id,
      company: d.company,
      already_reached_count: d.already_reached_count,
      candidates: (d.candidates ?? []).map(toCandidate),
      discover_state: (d.discover_state ?? "never") as ReferralCandidates["discover_state"],
      company_confirm: (d.company_confirm ?? []) as unknown as ReferralCandidates["company_confirm"],
      confirm_url_failed: Boolean(d.confirm_url_failed),
      refusal_reason: d.refusal_reason ?? "",
      in_flight_contact_ids: [...(d.in_flight_contact_ids ?? [])],
    };
  }

  async discoverReferrals(
    jobId: string,
    limit = 10,
    confirm?: CompanyConfirmPick,
    page = 1,
  ): Promise<string> {
    const body: Record<string, unknown> = { limit, page };
    if (confirm?.companyUrl) {
      body.company_url = confirm.companyUrl;
    } else if (confirm?.companyUrn) {
      body.company_urn = confirm.companyUrn;
      body.company_name = confirm.companyName ?? "";
      body.company_vanity = confirm.companyVanity ?? "";
      body.company_industry = confirm.companyIndustry ?? "";
    }
    const d = (await this.json(
      "POST",
      `/api/jobs/${jobId}/referrals/discover`,
      body,
    )) as { id: string };
    return d.id;
  }

  /** Grounded LLM rewrite of a contact's referral draft (US-REF-03 Regenerate).
   *  Enqueues a `draft` op; the drafted message lands in the operation's
   *  `result_ref` — the caller reads it via `getOperation` (not returned here). */
  async draftReferral(contactId: string, jobId?: string | null): Promise<string> {
    const d = (await this.json(
      "POST",
      `/api/contacts/${contactId}/draft`,
      { job_id: jobId ?? null },
    )) as { id: string };
    return d.id;
  }

  async reachOut(input: ReachOutInput): Promise<ReachOutResult> {
    const d = (await this.json("POST", "/api/referrals/reach-out", {
      job_id: input.job_id ?? null,
      application_id: input.application_id ?? null,
      dry_run: input.dry_run ?? false,
      contacts: input.contacts,
    })) as { enqueued: string[]; skippedContactIds?: string[] };
    return { enqueued: d.enqueued, skipped_contact_ids: d.skippedContactIds ?? [] };
  }

  async getReferralQuota(): Promise<ReferralQuota> {
    const d = await this.req<QuotaDTO>("/api/referrals/quota");
    return {
      connected: d.connected,
      daily_used: d.daily_used,
      daily_limit: d.daily_limit,
      weekly_used: d.weekly_used,
      weekly_limit: d.weekly_limit,
      dm_daily_sent: d.dm_daily_sent ?? 0,
      dm_weekly_sent: d.dm_weekly_sent ?? 0,
      dm_daily_limit: d.dm_daily_limit ?? 0,
    };
  }

  async getLinkedInSession(): Promise<LinkedInSessionState> {
    return toLinkedInSession(await this.req<LinkedInSessionDTO>("/api/linkedin/session"));
  }

  // ── LinkedIn session capture (US-SET-06 / N4) — the connect/enable controls
  // live in Settings (not built on this repo yet); these methods are restored
  // so Settings can wire them up without another real.ts pass. ─────────────
  async connectLinkedIn(): Promise<string> {
    const d = (await this.json("POST", "/api/linkedin/connect", {})) as { id: string };
    return d.id;
  }

  async disconnectLinkedIn(): Promise<LinkedInSessionState> {
    return toLinkedInSession((await this.json(
      "POST", "/api/linkedin/disconnect", {},
    )) as LinkedInSessionDTO);
  }

  async validateLinkedIn(): Promise<LinkedInSessionState> {
    return toLinkedInSession((await this.json(
      "POST", "/api/linkedin/validate", {},
    )) as LinkedInSessionDTO);
  }

  async resumeLinkedIn(): Promise<LinkedInSessionState> {
    return toLinkedInSession((await this.json(
      "POST", "/api/linkedin/resume", {},
    )) as LinkedInSessionDTO);
  }

  /** Refresh contact statuses from LinkedIn (FR-NW-15). Manual-only: the Sync
   *  button is the one caller (maintainer decision, 2026-08-15) — no on-open
   *  refresh, no schedule. See `docs/internal/linkedin-addon.md` section 5. */
  async syncContacts(): Promise<ContactSyncResult> {
    const dto = (await this.json("POST", "/api/networking/contact-sync", {})) as {
      id?: string | null; state: string;
    };
    return { id: dto.id ?? null, state: dto.state as ContactSyncResult["state"] };
  }

  /** Set the self-imposed LinkedIn rate-limit profile (2026-08-01). Pass the
   *  basis (`membership_type` and/or `risk_pct`) — which resets every override —
   *  OR one `override_key`/`override_value` pin, OR `reset_overrides`. */
  async setLinkedInRateLimits(body: {
    membership_type?: string;
    risk_pct?: number;
    override_key?: string;
    override_value?: number;
    reset_overrides?: boolean;
  }): Promise<LinkedInSessionState> {
    return toLinkedInSession((await this.json(
      "POST", "/api/linkedin/rate-limits", body,
    )) as LinkedInSessionDTO);
  }

  /** Queue a `view_page` op that shows `url` on the watch-only browser surface.
   *  Always 202: the view waits behind whatever drives the surface, and a view of
   *  the page already shown is a no-op. Sends display metrics (a first navigation
   *  fails closed without them); `contactId` names the ledger row's subject. */
  async viewInBrowser(url: string, surface?: string, contactId?: string): Promise<void> {
    await this.json("POST", "/api/browser/view", {
      url,
      surface,
      width: screen.width,
      height: screen.height,
      dpr: window.devicePixelRatio,
      contact_id: contactId,
    });
  }
}
