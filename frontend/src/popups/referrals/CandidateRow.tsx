// One referral-candidate row (extracted from ReferralsModal.tsx 2026-07-25,
// F-M6 monolith split — pure move, zero behavior change). Memoized: every prop
// is a primitive, a query-stable object (`c`), or a root-stable useCallback, so
// typing in one row's draft no longer re-renders every other row.
//
// 2026-08-16 (maintainer): when CONNECTED, the row no longer expands into its
// own draft editor — the pre-send confirmation is the one editable box (edit
// where you confirm). The expandable editor remains only in drafts-only manual
// mode, where no confirm dialog exists and Copy needs a visible draft.

import { memo } from "react";
import { useTranslation } from "react-i18next";

import type { ReferralCandidate } from "../../api/types";
import i18n from "../../i18n";
import { audienceTag } from "../../shell/audienceTag";
import { Avatar } from "../../shell/Avatar";

/** Ordinal degree label, or null when the degree is genuinely unknown (so the
 *  badge is hidden rather than rendering "NULLTH deg"). */
function degreeLabel(degree: number | null): string | null {
  if (degree == null) return null;
  if (degree === 1) return i18n.t("popups.referrals.degree.first");
  if (degree === 2) return i18n.t("popups.referrals.degree.second");
  if (degree === 3) return i18n.t("popups.referrals.degree.third");
  return i18n.t("popups.referrals.degree.nth", { degree });
}

export const CandidateRow = memo(function CandidateRow({
  c,
  connected,
  sendable,
  draft,
  expanded,
  sending,
  failure,
  onAsk,
  onExpand,
  onDraft,
  onOpenLinkedIn,
}: {
  c: ReferralCandidate;
  connected: boolean;
  /** May this row's Connect/Message button fire right now (session live, that
   *  channel's cap not exhausted, no send already in flight for this row)? */
  sendable: boolean;
  draft: string;
  expanded: boolean;
  sending: boolean;
  failure: string | null;
  /** Open the pre-send confirmation for THIS contact (per-contact confirm —
   *  the multi-select batch went away 2026-07-30; posture doc section 5.1). */
  onAsk: (id: string) => void;
  onExpand: (id: string) => void;
  onDraft: (id: string, v: string) => void;
  /** Open this contact's profile on the in-app LinkedIn view (2026-08-16 —
   *  never an external browser). While THIS row's send is driving the live
   *  surface the button glows instead, and clicking it just watches the send.
   *  Root-stable (id-passing). */
  onOpenLinkedIn: (id: string) => void;
}) {
  const { t } = useTranslation();
  const degLabel = degreeLabel(c.degree);
  const tag = audienceTag(c.audience_tag);
  return (
    <div className="border-b border-border" data-testid="referrals-row">
      <div className="flex items-center gap-3 px-5 py-3">
        {c.already_reached ? (
          <span className="inline-flex h-[18px] items-center rounded-full border border-good bg-good-wash px-1.5 text-[10px] text-good" data-testid="referrals-row-reached">
            {t("popups.referrals.rowReached")}
          </span>
        ) : sending ? (
          <span className="inline-flex h-[18px] items-center gap-1 rounded-full border border-warn bg-warn-wash px-1.5 text-[10px] text-warn" data-testid="referrals-row-sending">
            <span className="inline-block h-2 w-2 animate-spin rounded-full border border-warn border-t-transparent" />
            {t("popups.referrals.rowSending")}
          </span>
        ) : (
          <span className="h-4 w-4" />
        )}
        <Avatar name={c.name} shape="full" tone="raised" />
        <button
          className={"min-w-0 flex-1 text-left" + (connected ? " cursor-default" : "")}
          onClick={() => {
            if (!connected) onExpand(c.contact_id);
          }}
        >
          <div className="flex items-center gap-2 text-[13px] font-medium text-ink">
            <span className="truncate">{c.name}</span>
            {degLabel ? (
              <span className="inline-flex h-[16px] items-center rounded-full border border-border-2 bg-surface px-1.5 text-[9.5px] text-ink-3" data-testid="referrals-row-degree">
                {t("popups.referrals.degreeBadge", { degree: degLabel })}
              </span>
            ) : null}
            <span className={`inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10px] ${tag.cls}`} data-testid="referrals-row-tag">
              {t(tag.shortLabelKey)}
            </span>
          </div>
          <div className="truncate text-[11.5px] text-ink-3">{c.role} · {c.company}</div>
          {failure ? (
            <div className="mt-1 rounded border border-bad/40 bg-bad-wash px-1.5 py-1 text-[10.5px] leading-snug text-bad" data-testid="referrals-row-failure">
              {t("popups.referrals.notSent", { reason: failure })}
            </div>
          ) : null}
        </button>
        {/* Open the contact's profile on the IN-APP LinkedIn view (US-REF
            verifiability; 2026-08-16 — never an external browser). While this
            row's send is in flight the same button glows — the action-in-
            progress signal — and clicking it watches the send live. */}
        {c.linkedin_url ? (
          <button
            type="button"
            data-testid="referrals-row-linkedin"
            title={t("popups.referrals.verifyProfileTooltip")}
            onClick={(e) => {
              e.stopPropagation();
              onOpenLinkedIn(c.contact_id);
            }}
            className={
              "rounded-md border px-2 py-1 text-[11px] font-medium " +
              (sending
                ? "animate-pulse border-accent bg-surface text-accent ring-1 ring-accent/40"
                : "border-border-2 bg-surface text-ink-2 hover:bg-surface-3")
            }
          >
            {t("popups.referrals.linkedIn")}
          </button>
        ) : null}
        {/* Row-wise send: each contact has its own Connect (cold invite+note)
            or Message (warm 1st-degree DM) button that opens the pre-send
            confirmation for exactly this person. Replaces the checkbox
            multi-select + "Reach out (N)" batch (2026-07-30). The slot holds
            a FIXED footprint in every connected state (maintainer,
            2026-08-16: the vanishing button shifted the LinkedIn button
            between rows): actionable → the button; mid-send → the same
            button, disabled; already reached → the dull box below. */}
        {connected && !c.already_reached && (
          <button
            type="button"
            data-testid="referrals-row-send"
            disabled={!sendable}
            onClick={(e) => {
              e.stopPropagation();
              onAsk(c.contact_id);
            }}
            className="inline-flex w-[80px] items-center justify-center rounded-md border border-accent bg-accent px-2.5 py-1 text-[11px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {c.channel === "dm"
              ? t("popups.referrals.rowMessage")
              : t("popups.referrals.rowConnect")}
          </button>
        )}
        {/* Already reached: the same-size box, deliberately dull (a span, no
            hover, muted ink — clearly quieter than the LinkedIn button) so
            the row states the fact without offering a dead control. */}
        {connected && c.already_reached && (
          <span
            data-testid="referrals-row-requested"
            className="inline-flex w-[80px] select-none items-center justify-center rounded-md border border-border bg-surface-2 px-2.5 py-1 text-[11px] font-medium text-ink-4"
          >
            {c.channel === "dm"
              ? t("popups.referrals.rowMessaged")
              : t("popups.referrals.rowRequested")}
          </span>
        )}
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
      {expanded && !connected && (
        <div className="px-[52px] pb-4">
          <div className="mb-1 text-[10px] text-ink-3">
            {c.channel === "dm" ? t("popups.referrals.draftDm") : t("popups.referrals.draftConnection")}
          </div>
          {draft ? (
            <textarea
              data-testid="referrals-draft-textarea"
              value={draft}
              onChange={(e) => onDraft(c.contact_id, e.target.value)}
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
});
