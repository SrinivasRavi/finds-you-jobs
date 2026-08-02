// Find / view referrals popup — the centerpiece of Track N3 (US-NW-09 / US-REF-*),
// restored 2026-07-16 from the prior repo (the referral-outreach backend now
// exists on this sidecar).
//
// State machine (ports assets/shell.js openReferralsModal):
//   searching → review (per-row draft edit + per-row Connect/Message) → confirm
// Reworked 2026-07-30: the checkbox multi-select + "Reach out (N)" batch became
// a per-row Connect (cold invite+note) / Message (warm DM) button — each opens
// a pre-send confirmation showing THAT person's message, and sends exactly one
// contact (per-contact confirm; posture doc §5.1). Rows show Sending/Reached
// badges in place, so there is no full-screen "sending" phase any more.
// With the LinkedIn master toggle ON + a valid session, sends go through the
// networker module (the voyager quota/caps + backoff are surfaced here). With
// it OFF (default) or no session, the popup is drafts-only: copy each message
// and send it yourself — the module never sends (matches README + vision).
// Discovery/sends are streamed as `networker` SSE events. The LinkedIn enable
// toggle itself lives in Settings — this modal only reads session state.
//
// Split 2026-07-25 (F-M6): the per-row, quota-bar, company-confirm, and
// send-confirm pieces live in ./referrals/*; this file keeps the state machine.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { eventBus, type SSEEvent } from "../api/events";
import {
  useDiscoverReferrals,
  useLinkedInSession,
  useReachOut,
  useReferralCandidates,
  useReferralQuota,
} from "../api/queries";
import type { CompanyCandidate } from "../api/types";
import i18n from "../i18n";
import { Modal } from "../shell/Modal";
import { CandidateRow } from "./referrals/CandidateRow";
import { CompanyConfirmStep } from "./referrals/CompanyConfirmStep";
import { QuotaBar } from "./referrals/QuotaBar";
import { ReachOutConfirm } from "./referrals/ReachOutConfirm";

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

type Phase = "start" | "searching" | "confirm" | "review";

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
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  // The contact whose pre-send confirmation is open (per-contact confirm —
  // each row's Connect/Message button targets exactly one person).
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  // Per-contact send outcomes, streamed as `networker` SSE events: contacts with
  // a send in flight (spinner) and verbatim per-contact failures (US-NW-09). Before
  // this, a failed send surfaced nowhere — the maintainer only learned by checking
  // LinkedIn.
  const [sendingIds, setSendingIds] = useState<Set<string>>(new Set());
  const [failures, setFailures] = useState<Record<string, string>>({});
  const [skippedCount, setSkippedCount] = useState(0);
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

  // Seed each row's editable draft once, when the full connected review list
  // first lands (US-NW-09). (No pre-selection any more — there is no selection;
  // each row sends itself via its own button.)
  useEffect(() => {
    if (phase !== "review" || candidates.length === 0) return;
    setDrafts((prev) => {
      const next = { ...prev };
      for (const c of candidates) if (next[c.contact_id] === undefined) next[c.contact_id] = c.draft;
      return next;
    });
  }, [phase, candidates]);

  const dailyRemaining = quota.data ? quota.data.daily_limit - quota.data.daily_used : 99;
  const capReached = dailyRemaining <= 0;
  // DMs have their own budget (2026-07-30): a spent invite cap must not block a
  // warm 1st-degree Message, and vice versa. Limit 0 means "unknown" (stale
  // sidecar) — treat as not blocked; the package refuses authoritatively anyway.
  const dmCapReached = Boolean(
    quota.data && quota.data.dm_daily_sent >= quota.data.dm_daily_limit,
  );
  const failureCount = Object.keys(failures).length;

  // Stable per-row callbacks (id-passing) so the memoized CandidateRow only
  // re-renders when its own props change — not on every keystroke in a sibling
  // row's draft.
  const handleAsk = useCallback((id: string) => {
    setConfirmingId(id);
  }, []);

  const handleExpand = useCallback((id: string) => {
    setExpanded((cur) => (cur === id ? null : id));
  }, []);

  const handleDraft = useCallback((id: string, v: string) => {
    setDrafts((d) => ({ ...d, [id]: v }));
  }, []);

  async function doReachOut(contactId: string) {
    // Dedup guard: ignore a repeated Send now while THIS contact is in flight
    // (the sidecar also skips duplicates, but this stops the click from firing).
    if (reachOut.isPending || sendingIds.has(contactId)) return;
    setConfirmingId(null);
    const message =
      drafts[contactId] ?? candidates.find((c) => c.contact_id === contactId)?.draft ?? "";
    // Immediate feedback: the row shows a Sending badge before the request
    // resolves; the SSE outcome flips it to Reached/failed. The list stays on
    // screen — pacing spaces real sends 30-90 s apart, so a full-screen
    // "sending…" takeover would just hide the roster for minutes.
    setSendingIds((prev) => new Set(prev).add(contactId));
    setFailures((prev) => omit(prev, contactId));
    try {
      const res = await Promise.resolve(
        reachOut.mutateAsync({
          job_id: jobId,
          application_id: applicationId,
          contacts: [{ contact_id: contactId, message }],
        }),
      );
      // A duplicate the sidecar refused never entered flight — drop the spinner.
      if ((res?.skipped_contact_ids ?? []).includes(contactId)) {
        setSkippedCount((n) => n + 1);
        setSendingIds((prev) => remove(prev, contactId));
      }
      await candidatesQ.refetch();
      await quota.refetch();
    } catch {
      // The send never entered flight (the global MutationCache hook logs +
      // banners it) — un-stick the spinner so the user can retry.
      setSendingIds((prev) => remove(prev, contactId));
    }
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
    : finding ? t("popups.referrals.titleFinding")
    : alreadyReached > 0 ? t("popups.referrals.titleView") : t("popups.referrals.findReferrals");
  const title = `${titleVerb} — ${jobTitle} · ${company}`;

  return (
    <Modal title={title} onClose={onClose} width={1300}>
      <div className="flex h-[80vh] flex-col" data-testid="find-referrals-popup">
        {/* Quota / status bar */}
        {phase !== "start" && phase !== "searching" && phase !== "confirm" && (
          <QuotaBar
            alreadyReached={alreadyReached}
            connected={connected}
            quota={quota.data}
            capReached={capReached}
            dailyRemaining={dailyRemaining}
          />
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

        {/* Outcome summary — failures + skipped duplicates (US-NW-09). Shown in
            review as sends resolve; rows carry the per-contact detail. */}
        {phase === "review" && (failureCount > 0 || skippedCount > 0) && (
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
          <CompanyConfirmStep
            company={company}
            companyCandidates={companyCandidates}
            pickedCompany={pickedCompany}
            urlFailed={urlFailed}
            discoverPending={discover.isPending}
            pasteUrl={pasteUrl}
            onPasteUrl={setPasteUrl}
            onPick={setPickedCompany}
            onConfirmPicked={confirmPickedCompany}
            onConfirmUrl={(url) => void runConfirmed({ companyUrl: url })}
            onBack={() => setPhase("review")}
            onClose={onClose}
          />
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

          {phase === "review" &&
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
                  sendable={
                    connected &&
                    !c.already_reached &&
                    !sendingIds.has(c.contact_id) &&
                    // Channel-specific budget: invites and DMs are separate meters.
                    (c.channel === "dm" ? !dmCapReached : !capReached)
                  }
                  draft={drafts[c.contact_id] ?? c.draft}
                  expanded={expanded === c.contact_id}
                  sending={sendingIds.has(c.contact_id)}
                  failure={failures[c.contact_id] ?? null}
                  onAsk={handleAsk}
                  onExpand={handleExpand}
                  onDraft={handleDraft}
                />
              ))
            ))}

          {/* Discover more (FR-NW-01/02) — pulls the next batch of candidates.
              Shown when connected (voyager-driven); manual mode has no roster. */}
          {connected && phase === "review" && (
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

        {/* Footer — sending moved into the rows (per-row Connect/Message), so
            only Close + the company-confirm shortcut live here now. */}
        {phase === "review" && (
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
          </div>
        )}
      </div>

      {/* Pre-send confirmation for the one contact whose button was clicked
          (per-contact confirm — US-NW-09 / vision / posture doc §5.1). */}
      {confirmingId && (() => {
        const c = candidates.find((x) => x.contact_id === confirmingId);
        if (!c) return null;
        return (
          <ReachOutConfirm
            name={c.name}
            channel={c.channel}
            message={drafts[c.contact_id] ?? c.draft}
            sending={reachOut.isPending || sendingIds.has(c.contact_id)}
            onCancel={() => setConfirmingId(null)}
            onSend={() => void doReachOut(c.contact_id)}
          />
        );
      })()}
    </Modal>
  );
}
