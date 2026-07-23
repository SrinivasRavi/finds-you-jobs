// Find / view referrals popup — the centerpiece of Track N3 (US-NW-09 / US-REF-*),
// restored 2026-07-16 from the prior repo (the referral-outreach backend now
// exists on this sidecar).
//
// State machine (ports assets/shell.js openReferralsModal):
//   searching → review (multi-select + per-row draft edit) → confirm → sending → done
// With the LinkedIn master toggle ON + a valid session, the Reach out path sends
// through the networker module (per-action confirmation before anything leaves;
// the voyager quota/caps + backoff are surfaced here). With it OFF (default) or
// no session, the popup is drafts-only: copy each message and send it yourself —
// the module never sends (matches README + vision). Discovery/sends are streamed
// as `networker` SSE events. The LinkedIn enable toggle itself lives in Settings
// (not built on this repo yet) — this modal only reads session state.

import { useEffect, useMemo, useRef, useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { eventBus, type SSEEvent } from "../api/events";
import {
  useDiscoverReferrals,
  useLinkedInSession,
  useReachOut,
  useReferralCandidates,
  useReferralQuota,
} from "../api/queries";
import type { AudienceTag, CompanyCandidate, ReferralCandidate } from "../api/types";
import i18n from "../i18n";
import { Modal } from "../shell/Modal";

const BUDGET = 10; // recommended-max reaches per role (US-NW-09; not hard-enforced)

// i18n key map — translated with t(...) at render.
const TAG_LABEL: Record<AudienceTag, string> = {
  peer: "popups.referrals.tag.peer",
  hm: "popups.referrals.tag.hm",
  recruiter: "popups.referrals.tag.recruiter",
  leadership: "popups.referrals.tag.leadership",
  other: "popups.referrals.tag.peer",
};
const TAG_CLASS: Record<AudienceTag, string> = {
  peer: "border-border-2 bg-surface text-ink-2",
  hm: "border-accent bg-accent-wash text-accent-ink",
  recruiter: "border-pink bg-pink-wash text-pink",
  leadership: "border-purple bg-purple-wash text-purple",
  other: "border-border-2 bg-surface text-ink-2",
};

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
}

function remove(set: Set<string>, id: string): Set<string> {
  if (!set.has(id)) return set;
  const next = new Set(set);
  next.delete(id);
  return next;
}

function omit(map: Record<string, string>, id: string): Record<string, string> {
  if (!(id in map)) return map;
  const rest = { ...map };
  delete rest[id];
  return rest;
}

type Phase = "start" | "searching" | "confirm" | "review" | "sending" | "done";

export function ReferralsModal({
  jobId,
  jobTitle,
  company,
  applicationId,
  onClose,
}: {
  jobId: string;
  jobTitle: string;
  company: string;
  applicationId?: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const session = useLinkedInSession();
  const connected = Boolean(session.data?.enabled && session.data.status === "valid");
  const quota = useReferralQuota();
  const candidatesQ = useReferralCandidates(jobId);
  const discover = useDiscoverReferrals();
  const reachOut = useReachOut();

  const candidates = useMemo(() => candidatesQ.data?.candidates ?? [], [candidatesQ.data]);
  const alreadyReached = candidatesQ.data?.already_reached_count ?? 0;

  const [phase, setPhase] = useState<Phase>("searching");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  // Per-contact send outcomes, streamed as `networker` SSE events: contacts with
  // a send in flight (spinner) and verbatim per-contact failures (US-NW-09). Before
  // this, a failed send surfaced nowhere — the maintainer only learned by checking
  // LinkedIn.
  const [sendingIds, setSendingIds] = useState<Set<string>>(new Set());
  const [failures, setFailures] = useState<Record<string, string>>({});
  const [skippedCount, setSkippedCount] = useState(0);
  const userTouchedRef = useRef(false);
  // Company-confirm step (FR-NW-02): when discovery can't auto-pick the target
  // company (ambiguous name / no employer-domain match), the sidecar streams a
  // `needs_company_confirm` event with the candidate entities and discovers
  // nothing; the user picks one here, then we re-run discovery scoped to it.
  const [companyCandidates, setCompanyCandidates] = useState<CompanyCandidate[]>([]);
  const [pickedCompany, setPickedCompany] = useState<string | null>(null);
  const [pasteUrl, setPasteUrl] = useState("");
  const [urlFailed, setUrlFailed] = useState(false);
  // "Find 10 more" fetches the NEXT results page (not a bigger page-1); merged
  // into the pool by upsert.
  const [page, setPage] = useState(1);
  // True while a discover op is actually running server-side (which takes ~30s —
  // far longer than the HTTP submit). Cleared by the `discovered`/`send_failed`
  // SSE event. Drives the title + button "finding" indicator (a submitted-but-
  // not-done op is invisible to `discover.isPending`).
  const [discovering, setDiscovering] = useState(false);
  const discoverOpIdRef = useRef<string | null>(null);
  // Guards the boot discovery so it fires AT MOST ONCE per open, even if the
  // effect re-runs (a settling `connected` query, a re-mount). Without it the
  // modal fired two concurrent discover ops ~2 ms apart — two live LinkedIn
  // scans, and the second op's late `needs_company_confirm` re-opened the
  // company picker after the user had already confirmed (the confirm→search→
  // ask-again loop; 2026-07-13 debug: two no-URN discover ops at the same ms).
  const bootedRef = useRef(false);

  // Subscribe to the send-outcome stream for this role: sent → clear sending +
  // any prior failure; send_failed → clear sending + record the verbatim reason.
  useEffect(() => {
    const off = eventBus.subscribe((ev: SSEEvent) => {
      if (ev.type === "operation") {
        const op = ev.payload as { id?: string; state?: string };
        if (
          op.id === discoverOpIdRef.current &&
          (op.state === "succeeded" || op.state === "failed")
        ) {
          discoverOpIdRef.current = null;
          setDiscovering(false);
          void candidatesQ.refetch();
          setPhase((cur) => (cur === "searching" ? "review" : cur));
        }
        return;
      }
      if (ev.type !== "networker") return;
      const p = ev.payload as {
        id?: string; phase?: string; contact_id?: string; job_id?: string; reason?: string;
        candidates?: CompanyCandidate[]; url_failed?: boolean;
      };
      if (p.job_id && p.job_id !== jobId) return;
      // Company disambiguation for THIS role — surface the picker (no contact_id).
      if (p.phase === "needs_company_confirm") {
        // Only the CURRENTLY-active discover op may open the picker. A superseded
        // op (e.g. a duplicate boot scan) firing its own late needs-confirm must
        // not re-open the picker after the user already confirmed the company.
        if (p.id && discoverOpIdRef.current && p.id !== discoverOpIdRef.current) return;
        const cands = p.candidates ?? [];
        setCompanyCandidates(cands);
        setPickedCompany(cands[0] ? cands[0].urn || `v:${cands[0].vanity}` : null);
        setUrlFailed(Boolean(p.url_failed));
        setDiscovering(false);
        setPhase("confirm");
        return;
      }
      // Discovery finished (roster ready) — stop the "finding" indicator.
      if (p.phase === "discovered") {
        setDiscovering(false);
        void candidatesQ.refetch();
        return;
      }
      if (!p.contact_id) return;
      if (p.phase === "sent") {
        setSendingIds((prev) => remove(prev, p.contact_id as string));
        setFailures((prev) => omit(prev, p.contact_id as string));
      } else if (p.phase === "send_failed") {
        setSendingIds((prev) => remove(prev, p.contact_id as string));
        setFailures((prev) => ({
          ...prev,
          [p.contact_id as string]: p.reason || i18n.t("popups.referrals.sendFailedFallback"),
        }));
      }
    });
    return off;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- candidatesQ.refetch is stable
  }, [jobId]);

  // On open: connected → show any existing roster instantly, else land in the
  // idle `start` phase and wait for an explicit "Find referrals" click — parity
  // with the Resume/Cover modals, which never auto-generate (2026-07-13). Not
  // connected → drafts-only review over whatever contacts already exist for this
  // company (the module never sends).
  useEffect(() => {
    let cancelled = false;
    async function boot() {
      if (connected) {
        // A roster from an earlier run (auto-discover-on-Save, a previous
        // open) shows instantly — discovery only runs when there's nothing
        // yet (2026-07-12 feedback: it re-searched on every open).
        const existing = await candidatesQ.refetch();
        const data = existing.data;
        if ((data?.candidates?.length ?? 0) > 0) {
          if (!cancelled) setPhase("review");
          return;
        }
        if (bootedRef.current) return;
        // Recover the last discover's outcome (2026-07-17): a background
        // Save-discover that needed company confirmation used to vanish into a
        // blank start screen. Resurface the picker (or the honest empty state)
        // instead of pretending nothing ran.
        if (data?.discover_state === "confirm") {
          setCompanyCandidates(data.company_confirm ?? []);
          setUrlFailed(Boolean(data.confirm_url_failed));
          if (!cancelled) setPhase("confirm");
          return;
        }
        // Empty roster: idle `start` screen with an explicit Find-referrals
        // button (its copy notes when a prior scan found nobody). Nothing
        // scans until the user asks.
        if (!cancelled) setPhase("start");
      } else {
        setPhase("review");
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Explicit "Find referrals" — the only path that starts a boot discovery
  // (idempotent — merges into the company pool). Fires at most once per open
  // (bootedRef), so a double-click never launches two concurrent LinkedIn scans.
  async function startDiscovery() {
    if (bootedRef.current || discovering) return;
    bootedRef.current = true;
    setPhase("searching");
    setDiscovering(true);
    try {
      discoverOpIdRef.current = await Promise.resolve(discover.mutateAsync(jobId));
    } catch {
      // A refused submit (e.g. one already running) must never strand the
      // spinner — fall through to review; SSE/ops events keep updating us.
      setDiscovering(false);
    }
    await candidatesQ.refetch();
    // A `needs_company_confirm` event may have flipped us into the picker while
    // discovery ran — don't clobber it with review.
    setPhase((cur) => (cur === "confirm" ? cur : "review"));
  }

  // Seed each row's editable draft + pre-select up to 5 not-yet-reached
  // candidates once, when the full connected review list first lands (US-NW-09).
  useEffect(() => {
    if (phase !== "review" || candidates.length === 0) return;
    setDrafts((prev) => {
      const next = { ...prev };
      for (const c of candidates) if (next[c.contact_id] === undefined) next[c.contact_id] = c.draft;
      return next;
    });
    // Restore the persisted selection (FR-NW-01): a reopened `pending` popup
    // shows the contacts the user already picked (minus any now reached). When
    // there is no persisted selection, pre-select the first ≤5 not-yet-reached
    // candidates. Recomputed as the list grows until the user touches it.
    if (connected && !userTouchedRef.current) {
      const persisted = candidates.filter((c) => c.already_selected && !c.already_reached);
      if (persisted.length > 0) {
        setSelected(new Set(persisted.map((c) => c.contact_id)));
      } else {
        const pick = new Set<string>();
        for (const c of candidates) {
          if (pick.size >= 5) break;
          if (!c.already_reached) pick.add(c.contact_id);
        }
        setSelected(pick);
      }
    }
  }, [phase, candidates, connected]);

  const remaining = Math.max(BUDGET - alreadyReached, 0);
  const dailyRemaining = quota.data ? quota.data.daily_limit - quota.data.daily_used : 99;
  const capReached = dailyRemaining <= 0;
  const failureCount = Object.keys(failures).length;

  function toggle(id: string) {
    userTouchedRef.current = true;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < remaining && !capReached) next.add(id);
      return next;
    });
  }

  async function doReachOut() {
    // Dedup guard: ignore a repeated Send now while a batch is already in flight
    // (the sidecar also skips duplicates, but this stops the click from firing).
    if (reachOut.isPending || sendingIds.size > 0) return;
    setConfirming(false);
    const picks = [...selected].map((id) => ({
      contact_id: id,
      message: drafts[id] ?? candidates.find((c) => c.contact_id === id)?.draft ?? "",
    }));
    // Immediate feedback: mark every picked contact as sending before the request
    // resolves; SSE outcomes flip each to sent/failed.
    setSendingIds(new Set(picks.map((p) => p.contact_id)));
    setFailures({});
    setSkippedCount(0);
    setPhase("sending");
    const res = await Promise.resolve(
      reachOut.mutateAsync({ job_id: jobId, application_id: applicationId, contacts: picks }),
    );
    // Duplicates the sidecar refused never entered flight — drop their spinners.
    const skipped = res?.skipped_contact_ids ?? [];
    if (skipped.length > 0) {
      setSkippedCount(skipped.length);
      setSendingIds((prev) => {
        const next = new Set(prev);
        for (const id of skipped) next.delete(id);
        return next;
      });
    }
    await candidatesQ.refetch();
    await quota.refetch();
    setSelected(new Set());
    setPhase("done");
  }

  // Re-run discovery scoped to a confirmed company (the sidecar caches the choice
  // for this employer). `confirm` is either a picked candidate or a pasted URL.
  async function runConfirmed(confirm: { companyUrn?: string; companyName?: string;
    companyVanity?: string; companyIndustry?: string; companyUrl?: string }) {
    if (discover.isPending) return;
    setUrlFailed(false);
    setPage(1);
    setPhase("searching");
    setDiscovering(true);
    try {
      discoverOpIdRef.current = await Promise.resolve(
        discover.mutateAsync({ jobId, limit: 10, page: 1, confirm }),
      );
    } catch {
      setDiscovering(false);
    }
    await candidatesQ.refetch();
    // A bad pasted URL re-emits needs_company_confirm → the SSE handler flips us
    // back to "confirm"; only advance to review when we didn't get bounced.
    setPhase((cur) => (cur === "confirm" ? cur : "review"));
  }

  function confirmPickedCompany() {
    const key = pickedCompany;
    const chosen = companyCandidates.find((c) => (c.urn || `v:${c.vanity}`) === key);
    if (!chosen) return;
    // A candidate with a resolved URN goes straight through; one we could only
    // scrape a vanity for is resolved authoritatively via its LinkedIn URL.
    if (chosen.urn) {
      void runConfirmed({
        companyUrn: chosen.urn, companyName: chosen.name,
        companyVanity: chosen.vanity, companyIndustry: chosen.industry,
      });
    } else if (chosen.vanity) {
      void runConfirmed({ companyUrl: `https://www.linkedin.com/company/${chosen.vanity}/` });
    }
  }

  function confirmPastedUrl() {
    const url = pasteUrl.trim();
    if (!url) return;
    void runConfirmed({ companyUrl: url });
  }

  // "Find 10 more" (FR-NW-01/02): fetch the NEXT results page and merge it into
  // the pool via upsert (not a bigger page-1, which returned the same faces).
  // Stays in review with a "finding" indicator so the growing list is visible.
  async function loadMore() {
    if (!connected || discovering) return;
    const next = page + 1;
    setPage(next);
    setDiscovering(true);
    try {
      discoverOpIdRef.current = await Promise.resolve(
        discover.mutateAsync({ jobId, limit: 10, page: next }),
      );
    } catch {
      setDiscovering(false);
    }
    await candidatesQ.refetch();
  }

  // Watchdog: discovery must never look alive forever (the 22-min spinner).
  useEffect(() => {
    if (!discovering) return;
    const t = setTimeout(() => {
      setDiscovering(false);
      void candidatesQ.refetch();
      setPhase((cur) => (cur === "searching" ? "review" : cur));
    }, 120_000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch is stable
  }, [discovering]);

  // Discovery is genuinely in flight (submit + the long server-side op).
  const finding = phase === "searching" || discovering;
  const titleVerb = phase === "confirm" ? t("popups.referrals.titleConfirmCompany")
    : phase === "sending" ? t("popups.referrals.titleSendingMessages")
    : finding ? t("popups.referrals.titleFinding")
    : alreadyReached > 0 ? t("popups.referrals.titleView") : t("popups.referrals.findReferrals");
  const title = `${titleVerb} — ${jobTitle} · ${company}`;

  return (
    <Modal title={title} onClose={onClose} width={1300}>
      <div className="flex h-[80vh] flex-col" data-testid="find-referrals-popup">
        {/* Quota / status bar */}
        {phase !== "start" && phase !== "searching" && phase !== "confirm" && (
          <div
            className="flex flex-col gap-1 border-b border-border bg-surface-2 px-5 py-2 text-[11.5px]"
            data-testid="referrals-quota-bar"
          >
            <div className="flex items-center gap-3">
              <span data-testid="referrals-quota-counter" className="font-mono text-ink-2">
                {t("popups.referrals.reachesSent", { count: alreadyReached })}
              </span>
              {/* Our conservative caps only apply when WE do the sending
                  (automation on + connected). In manual mode the user tracks
                  their own outreach against LinkedIn's real limits. */}
              {quota.data && connected ? (
                <>
                  <span className="text-ink-4">·</span>
                  <span
                    className="text-ink-3"
                    title={t("popups.referrals.quotaTooltip")}
                  >
                    <Trans
                      i18nKey="popups.referrals.automatedQuota"
                      values={{
                        dailyUsed: quota.data.daily_used,
                        dailyLimit: quota.data.daily_limit,
                        weeklyUsed: quota.data.weekly_used,
                        weeklyLimit: quota.data.weekly_limit,
                      }}
                      components={{ strong: <strong /> }}
                    />
                  </span>
                  <span className="text-ink-4">·</span>
                  <span
                    className="text-ink-3"
                    data-testid="referrals-dm-counter"
                    title={t("popups.referrals.dmTooltip")}
                  >
                    <Trans
                      i18nKey="popups.referrals.dmCounter"
                      values={{ dmSent: quota.data.dm_daily_sent }}
                      components={{ strong: <strong /> }}
                    />
                  </span>
                </>
              ) : (
                <>
                  <span className="text-ink-4">·</span>
                  <span className="text-ink-3">{t("popups.referrals.manualModeQuota")}</span>
                </>
              )}
            </div>
            {capReached && (
              <div className="rounded-md border border-bad bg-bad-wash px-3 py-1.5 font-medium text-bad" data-testid="quota-blocked">
                {t("popups.referrals.dailyLimitReached")}
              </div>
            )}
            {!capReached && dailyRemaining <= 5 && (
              <div className="rounded-md border border-warn bg-warn-wash px-3 py-1.5 font-medium text-warn">
                {t("popups.referrals.closeToLimit", { count: dailyRemaining })}
              </div>
            )}
          </div>
        )}

        {/* Manual-mode banner — automation off or not connected. Manual
            tracking is a first-class mode, not a degraded one. */}
        {!connected && phase === "review" && (
          <div className="border-b border-border bg-surface-2 px-5 py-2.5 text-[12px] text-ink-2" data-testid="referrals-drafts-only-banner">
            {session.data?.enabled
              ? t("popups.referrals.bannerNotConnected")
              : t("popups.referrals.bannerManualOff")}
          </div>
        )}

        {/* Outcome summary — failures + skipped duplicates (US-NW-09) */}
        {phase === "done" && (failureCount > 0 || skippedCount > 0) && (
          <div
            className="border-b border-border bg-surface-2 px-5 py-2 text-[11.5px]"
            data-testid="referrals-outcome-summary"
          >
            {failureCount > 0 && (
              <span className="mr-3 font-medium text-bad">
                <Trans
                  i18nKey="popups.referrals.sendsFailed"
                  count={failureCount}
                  components={{ code: <code /> }}
                />
              </span>
            )}
            {skippedCount > 0 && (
              <span className="text-ink-3">
                {t("popups.referrals.skipped", { count: skippedCount })}
              </span>
            )}
          </div>
        )}

        {/* Company-confirm step (FR-NW-02) — pick the right entity before we
            search its current employees. Single-select, distinct from the
            people multi-select in review. */}
        {phase === "confirm" && (
          <div className="flex flex-1 flex-col overflow-hidden" data-testid="company-confirm">
            <div className="border-b border-border bg-surface-2 px-5 py-3 text-[12.5px] text-ink-2">
              {companyCandidates.length > 0 ? (
                <Trans
                  i18nKey="popups.referrals.confirmIntroPick"
                  values={{ company }}
                  components={{ strong: <strong />, em: <em /> }}
                />
              ) : (
                <Trans
                  i18nKey="popups.referrals.confirmIntroNoMatch"
                  values={{ company }}
                  components={{ strong: <strong /> }}
                />
              )}
            </div>
            {urlFailed && (
              <div
                className="border-b border-border bg-bad-wash px-5 py-2 text-[12px] font-medium text-bad"
                data-testid="company-url-failed"
              >
                <Trans
                  i18nKey="popups.referrals.urlFailed"
                  components={{ code: <code className="mx-1" /> }}
                />
              </div>
            )}
            <div className="flex-1 overflow-y-auto">
              {companyCandidates.map((c) => (
                <label
                  key={c.urn}
                  data-testid="company-candidate"
                  className="flex cursor-pointer items-center gap-3 border-b border-border px-5 py-3 hover:bg-surface-2"
                >
                  <input
                    type="radio"
                    name="company-pick"
                    data-testid="company-candidate-radio"
                    checked={pickedCompany === (c.urn || `v:${c.vanity}`)}
                    onChange={() => setPickedCompany(c.urn || `v:${c.vanity}`)}
                    className="h-4 w-4 cursor-pointer"
                  />
                  {c.logo_url ? (
                    <img
                      src={c.logo_url}
                      alt=""
                      className="h-8 w-8 shrink-0 rounded-md object-cover"
                    />
                  ) : (
                    <span className="inline-grid h-8 w-8 shrink-0 place-items-center rounded-md bg-surface-3 font-mono text-[11px] font-semibold text-ink-2">
                      {initials(c.name)}
                    </span>
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-[13px] font-medium text-ink">
                      <span className="truncate">{c.name}</span>
                      {c.domain_match && (
                        <span className="inline-flex h-[16px] items-center rounded-full border border-good bg-good-wash px-1.5 font-mono text-[9.5px] text-good">
                          {t("popups.referrals.bestMatch")}
                        </span>
                      )}
                    </span>
                    {/* Subtitle: industry when known, else the company's slug —
                        something identifying, never a generic filler label. */}
                    <span className="block truncate text-[11.5px] text-ink-3">
                      {c.industry || (c.vanity ? `linkedin.com/company/${c.vanity}` : "")}
                    </span>
                  </span>
                  {/* Verify link — open the company's LinkedIn page in a new tab. */}
                  {c.vanity ? (
                    <a
                      href={`https://www.linkedin.com/company/${c.vanity}/`}
                      target="_blank"
                      rel="noopener noreferrer"
                      data-testid="company-candidate-link"
                      onClick={(e) => e.stopPropagation()}
                      className="shrink-0 rounded-md border border-border-2 bg-surface px-2 py-1 text-[11px] font-medium text-ink-2 hover:bg-surface-3"
                    >
                      {t("popups.referrals.linkedIn")}
                    </a>
                  ) : null}
                </label>
              ))}
            </div>
            {/* Paste the company's LinkedIn URL — the authoritative override */}
            <div className="flex items-center gap-2 border-t border-border bg-surface-2 px-5 py-2.5">
              <input
                type="url"
                data-testid="company-url-input"
                value={pasteUrl}
                onChange={(e) => setPasteUrl(e.target.value)}
                placeholder={t("popups.referrals.pasteUrlPlaceholder")}
                className="h-[30px] flex-1 rounded-md border border-border bg-surface px-2.5 text-[12px] text-ink focus:border-accent focus:outline-none"
              />
              <button
                data-testid="company-url-use-btn"
                disabled={!pasteUrl.trim() || discover.isPending}
                onClick={confirmPastedUrl}
                className="h-[30px] rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {t("popups.referrals.useThisUrl")}
              </button>
            </div>
            <div className="flex items-center gap-2 border-t border-border bg-surface-2 px-5 py-3">
              <button
                data-testid="company-confirm-back"
                className="h-[30px] rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
                onClick={() => setPhase("review")}
              >
                {t("popups.referrals.back")}
              </button>
              <span className="text-[11px] text-ink-4">
                {t("popups.referrals.backHint")}
              </span>
              <span className="flex-1" />
              <button
                className="h-[30px] rounded-md px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
                onClick={onClose}
              >
                {t("popups.referrals.cancel")}
              </button>
              <button
                data-testid="company-confirm-btn"
                disabled={!pickedCompany || discover.isPending}
                onClick={confirmPickedCompany}
                className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
              >
                {t("popups.referrals.findEmployees")}
              </button>
            </div>
          </div>
        )}

        {/* Body */}
        {phase !== "confirm" && (
        <div className="flex-1 overflow-y-auto">
          {phase === "start" && (
            <div
              className="flex h-full flex-col items-center justify-center gap-5 px-8 py-16 text-center"
              data-testid="referrals-start"
            >
              <div className="max-w-md">
                <div className="text-[14px] font-semibold text-ink">
                  {candidatesQ.data?.discover_state === "empty"
                    ? t("popups.referrals.startEmptyTitle")
                    : t("popups.referrals.startTitle")}
                </div>
                <div className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
                  {candidatesQ.data?.discover_state === "empty" ? (
                    <Trans
                      i18nKey="popups.referrals.startEmptyBody"
                      values={{ company }}
                      components={{ strong: <strong className="text-ink-2" /> }}
                    />
                  ) : (
                    <Trans
                      i18nKey="popups.referrals.startScanBody"
                      values={{ company }}
                      components={{ strong: <strong className="text-ink-2" /> }}
                    />
                  )}
                </div>
              </div>
              <button
                data-testid="referrals-find-btn"
                onClick={() => void startDiscovery()}
                className="inline-flex h-[34px] items-center gap-1.5 rounded-md border border-accent bg-accent px-4 text-[12.5px] font-medium text-white hover:bg-accent-ink"
              >
                {t("popups.referrals.findReferrals")}
              </button>
            </div>
          )}

          {phase === "searching" && (
            <div className="flex h-full flex-col items-center justify-center gap-4 py-16 text-center">
              <div className="h-9 w-9 animate-spin rounded-full border-2 border-accent border-t-transparent" />
              <div>
                <div className="text-[14px] font-semibold text-ink">{t("popups.referrals.findingContacts", { company })}</div>
                <div className="mt-1 text-[12px] text-ink-3">
                  {t("popups.referrals.scanningHint")}
                </div>
              </div>
            </div>
          )}

          {phase === "sending" && (
            <div className="flex h-full flex-col items-center justify-center gap-4 py-16 text-center" data-testid="referrals-sending">
              <div className="h-9 w-9 animate-spin rounded-full border-2 border-warn border-t-transparent" />
              <div>
                <div className="text-[14px] font-semibold text-ink">{t("popups.referrals.sendingTitle")}</div>
                <div className="mt-1 text-[12px] text-ink-3">
                  {t("popups.referrals.sendingHint")}
                </div>
              </div>
            </div>
          )}

          {(phase === "review" || phase === "done") &&
            (candidates.length === 0 ? (
              connected && discovering ? (
                // The 202 resolves long before the ~30s voyager op — an empty
                // roster while discovery runs is still "searching", never
                // "nothing found" (2026-07-11 beta feedback: cognitive dead end).
                <div className="flex h-full flex-col items-center justify-center gap-4 py-16 text-center">
                  <div className="h-9 w-9 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                  <div>
                    <div className="text-[14px] font-semibold text-ink">{t("popups.referrals.findingContacts", { company })}</div>
                    <div className="mt-1 text-[12px] text-ink-3">
                      {t("popups.referrals.scanningHint")}
                    </div>
                  </div>
                </div>
              ) : (
              <div className="flex h-full items-center justify-center px-8 text-center text-[13px] text-ink-3">
                {connected
                  ? t("popups.referrals.emptyConnected")
                  : t("popups.referrals.emptyManual")}
              </div>
              )
            ) : (
              candidates.map((c) => (
                <CandidateRow
                  key={c.contact_id}
                  c={c}
                  connected={connected}
                  selectable={connected && !c.already_reached && !capReached && !sendingIds.has(c.contact_id)}
                  checked={selected.has(c.contact_id)}
                  draft={drafts[c.contact_id] ?? c.draft}
                  expanded={expanded === c.contact_id}
                  sending={sendingIds.has(c.contact_id)}
                  failure={failures[c.contact_id] ?? null}
                  onToggle={() => toggle(c.contact_id)}
                  onExpand={() => setExpanded(expanded === c.contact_id ? null : c.contact_id)}
                  onDraft={(v) => setDrafts((d) => ({ ...d, [c.contact_id]: v }))}
                />
              ))
            ))}

          {/* Discover more (FR-NW-01/02) — pulls the next batch of candidates.
              Shown when connected (voyager-driven); manual mode has no roster. */}
          {connected && (phase === "review" || phase === "done") && (
            <div className="flex flex-col items-center gap-1.5 px-5 py-4">
              {candidates.length > 0 && (
                <div className="text-[11.5px] text-ink-3" data-testid="referrals-roster-count">
                  {t("popups.referrals.contactsFound", { count: candidates.length })}
                </div>
              )}
              <button
                data-testid="referrals-load-more"
                onClick={() => void loadMore()}
                disabled={discovering}
                className="inline-flex h-[30px] items-center gap-1.5 rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {discovering ? (
                  <>
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border border-ink-3 border-t-transparent" />
                    {t("popups.referrals.findingMore")}
                  </>
                ) : candidates.length === 0 ? (
                  t("popups.referrals.findMoreManual")
                ) : (
                  t("popups.referrals.findMore")
                )}
              </button>
            </div>
          )}
        </div>
        )}

        {/* Footer */}
        {(phase === "review" || phase === "done") && (
          <div className="flex items-center justify-end gap-2 border-t border-border bg-surface-2 px-5 py-3">
            {companyCandidates.length > 0 && (
              <button
                data-testid="company-confirm-next"
                className="mr-auto h-[30px] rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
                onClick={() => setPhase("confirm")}
              >
                {t("popups.referrals.confirmCompanyNext")}
              </button>
            )}
            <button
              className="h-[30px] rounded-md px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
              onClick={onClose}
            >
              {t("popups.referrals.close")}
            </button>
            {connected && (
              <button
                data-testid="referrals-reach-out-btn"
                disabled={selected.size === 0 || capReached}
                onClick={() => setConfirming(true)}
                className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
              >
                {t("popups.referrals.reachOut", { count: selected.size })}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Per-action confirmation before anything sends (US-NW-09 / vision) */}
      {confirming && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-[rgba(0,0,0,0.35)]" data-testid="reach-out-confirm">
          <div className="w-[380px] rounded-[12px] border border-border bg-surface p-5 shadow-xl">
            <h3 className="text-[14px] font-semibold text-ink">{t("popups.referrals.sendConfirmTitle", { count: selected.size })}</h3>
            <p className="mt-2 text-[12.5px] text-ink-3">
              <Trans
                i18nKey="popups.referrals.sendConfirmBody"
                components={{ span: <span className="text-ink-2" /> }}
              />
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button className="h-[30px] rounded-md px-3 text-[12px] text-ink-2 hover:bg-surface-2" onClick={() => setConfirming(false)}>
                {t("popups.referrals.cancel")}
              </button>
              <button
                data-testid="reach-out-confirm-btn"
                disabled={reachOut.isPending || sendingIds.size > 0}
                className="inline-flex h-[30px] items-center gap-1.5 rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-60"
                onClick={() => void doReachOut()}
              >
                {reachOut.isPending || sendingIds.size > 0 ? (
                  <>
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border border-white/60 border-t-transparent" />
                    {t("popups.referrals.sendingEllipsis")}
                  </>
                ) : (
                  t("popups.referrals.sendNow")
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Ordinal degree label, or null when the degree is genuinely unknown (so the
 *  badge is hidden rather than rendering "NULLTH deg"). */
function degreeLabel(degree: number | null): string | null {
  if (degree == null) return null;
  if (degree === 1) return i18n.t("popups.referrals.degree.first");
  if (degree === 2) return i18n.t("popups.referrals.degree.second");
  if (degree === 3) return i18n.t("popups.referrals.degree.third");
  return i18n.t("popups.referrals.degree.nth", { degree });
}

function CandidateRow({
  c,
  connected,
  selectable,
  checked,
  draft,
  expanded,
  sending,
  failure,
  onToggle,
  onExpand,
  onDraft,
}: {
  c: ReferralCandidate;
  connected: boolean;
  selectable: boolean;
  checked: boolean;
  draft: string;
  expanded: boolean;
  sending: boolean;
  failure: string | null;
  onToggle: () => void;
  onExpand: () => void;
  onDraft: (v: string) => void;
}) {
  const { t } = useTranslation();
  const degLabel = degreeLabel(c.degree);
  return (
    <div className="border-b border-border" data-testid="referrals-row">
      <div className="flex items-center gap-3 px-5 py-3">
        {c.already_reached ? (
          <span className="inline-flex h-[18px] items-center rounded-full border border-good bg-good-wash px-1.5 font-mono text-[10px] text-good" data-testid="referrals-row-reached">
            {t("popups.referrals.rowReached")}
          </span>
        ) : sending ? (
          <span className="inline-flex h-[18px] items-center gap-1 rounded-full border border-warn bg-warn-wash px-1.5 font-mono text-[10px] text-warn" data-testid="referrals-row-sending">
            <span className="inline-block h-2 w-2 animate-spin rounded-full border border-warn border-t-transparent" />
            {t("popups.referrals.rowSending")}
          </span>
        ) : selectable ? (
          <input
            type="checkbox"
            data-testid="referrals-row-checkbox"
            checked={checked}
            onChange={onToggle}
            className="h-4 w-4 cursor-pointer"
          />
        ) : (
          <span className="h-4 w-4" />
        )}
        <span className="inline-grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface-3 font-mono text-[11px] font-semibold text-ink-2">
          {initials(c.name)}
        </span>
        <button className="min-w-0 flex-1 text-left" onClick={onExpand}>
          <div className="flex items-center gap-2 text-[13px] font-medium text-ink">
            <span className="truncate">{c.name}</span>
            {degLabel ? (
              <span className="inline-flex h-[16px] items-center rounded-full border border-border-2 bg-surface px-1.5 font-mono text-[9.5px] text-ink-3" data-testid="referrals-row-degree">
                {t("popups.referrals.degreeBadge", { degree: degLabel })}
              </span>
            ) : null}
            <span className={`inline-flex h-[18px] items-center rounded-full border px-1.5 font-mono text-[10px] ${TAG_CLASS[c.audience_tag]}`} data-testid="referrals-row-tag">
              {t(TAG_LABEL[c.audience_tag])}
            </span>
          </div>
          <div className="truncate text-[11.5px] text-ink-3">{c.role} · {c.company}</div>
          {failure ? (
            <div className="mt-1 rounded border border-bad/40 bg-bad-wash px-1.5 py-1 text-[10.5px] leading-snug text-bad" data-testid="referrals-row-failure">
              {t("popups.referrals.notSent", { reason: failure })}
            </div>
          ) : null}
        </button>
        {/* Open the contact's LinkedIn profile — lets the user verify who this is
            (US-REF verifiability). Always shown; never trust discovery blindly. */}
        {c.linkedin_url ? (
          <a
            href={c.linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="referrals-row-linkedin"
            title={t("popups.referrals.verifyProfileTooltip")}
            onClick={(e) => e.stopPropagation()}
            className="rounded-md border border-border-2 bg-surface px-2 py-1 text-[11px] font-medium text-ink-2 hover:bg-surface-3"
          >
            {t("popups.referrals.linkedIn")}
          </a>
        ) : null}
        {!connected && (
          <button
            className="rounded-md border border-border-2 bg-surface px-2 py-1 text-[11px] text-ink-2 hover:bg-surface-3"
            data-testid="referrals-copy-btn"
            onClick={() => void navigator.clipboard?.writeText(draft)}
          >
            {t("popups.referrals.copy")}
          </button>
        )}
      </div>
      {expanded && (
        <div className="px-[52px] pb-4">
          <div className="mb-1 font-mono text-[10px] text-ink-3">
            {c.channel === "dm" ? t("popups.referrals.draftDm") : t("popups.referrals.draftConnection")}
          </div>
          {draft ? (
            <textarea
              data-testid="referrals-draft-textarea"
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              rows={3}
              className="w-full resize-none rounded-md border border-border bg-surface px-3 py-2 text-[12.5px] leading-relaxed text-ink focus:border-accent focus:outline-none"
            />
          ) : (
            <div
              className="flex items-center gap-2 rounded-md border border-border bg-surface-2 px-3 py-2 text-[12px] text-ink-3"
              data-testid="referrals-draft-loading"
            >
              <span className="inline-block h-3 w-3 animate-spin rounded-full border border-border-2 border-t-accent" />
              {t("popups.referrals.drafting")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
