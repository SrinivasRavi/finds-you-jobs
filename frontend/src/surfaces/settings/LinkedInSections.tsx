// LinkedIn session capture (US-SET-06 as-built) + the LinkedIn job search
// opt-in. (Extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure
// moves, zero behavior change.)
//
// Divergence from the prototype's cookie-paste form: the maintainer directed a
// **headed-browser login** — click Connect, a real browser opens at LinkedIn's
// login page, you log in yourself (incl. 2FA; the password never touches
// finds-you-jobs), and we save the session cookies once the `li_at` auth cookie
// appears. Status chip + connected-as + expiry + Validate/Disconnect/Resume
// mirror settings-linkedin.html.

import { memo, useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import {
  useConnectLinkedIn,
  useDisconnectLinkedIn,
  useLinkedinSearch,
  useLinkedInSession,
  useResumeLinkedIn,
  useSetLinkedInRateLimits,
  useSettings,
  useValidateLinkedIn,
} from "../../api/queries";
import type { LinkedInCap, LinkedInSessionState, Settings as SettingsT } from "../../api/types";
import { ConfirmDialog } from "../../shell/ConfirmDialog";
import { InfoDot } from "../../shell/InfoDot";
import { LinkedInOptIn } from "./LinkedInOptIn";
import { MUTED_WARN_BOX, Section } from "./shared";

// LinkedIn Job Search breaks ToS by SCRAPING listings (not messaging) — its own
// warning copy: user-clicked one-offs, one page of 25, no claims about how
// LinkedIn classifies the traffic.
const JOB_SEARCH_WARNING = "settingsPage.linkedinSearch.warning";

type PillVariant = { cls: string; dot: string; label: string };

// `label` is an i18n key — t()'d where the pill renders.
function statusPill(status: LinkedInSessionState["status"]): PillVariant {
  switch (status) {
    case "valid":
      return {
        cls: "bg-good-wash border-good-2 text-good",
        dot: "#1F9D55",
        label: "settingsPage.session.statusConnected",
      };
    case "connecting":
      return {
        cls: "bg-warn-wash border-warn-2 text-warn",
        dot: "#C5A24A",
        label: "settingsPage.session.statusConnecting",
      };
    case "backing_off":
      return {
        cls: "bg-bad-wash border-bad-2 text-bad",
        dot: "#B23A3A",
        label: "settingsPage.session.statusBackingOff",
      };
    case "expired":
      return {
        cls: "bg-bad-wash border-bad-2 text-bad",
        dot: "#B23A3A",
        label: "settingsPage.session.statusExpired",
      };
    default:
      return {
        cls: "bg-bad-wash border-bad-2 text-bad",
        dot: "#B23A3A",
        label: "settingsPage.session.statusDisconnected",
      };
  }
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

// Collapsible + SHARED (2026-07-23): one LinkedIn session drives both Referral
// Outreach and LinkedIn Job Search. Rendered in both places; because both read
// the same session query + hit the same backend, connecting or disconnecting in
// one reflects instantly in the other. Collapsed once connected to stay tidy.
// Memoized (no props): a parent ack/toggle re-render no longer re-renders it.
export const LinkedInSessionSection = memo(function LinkedInSessionSection() {
  const { t } = useTranslation();
  const { data: session } = useLinkedInSession();
  const connect = useConnectLinkedIn();
  const disconnect = useDisconnectLinkedIn();
  const validate = useValidateLinkedIn();
  const resume = useResumeLinkedIn();
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);
  const [resumeConfirm, setResumeConfirm] = useState(false);

  if (!session) return null;
  const status = session.status;
  const pill = statusPill(status);
  const connecting = status === "connecting" || connect.isPending;
  const connected = status === "valid";
  const open = openOverride ?? !connected; // expanded until connected, then tidy
  // Resume is the one control that switches a safety mechanism OFF — it clears
  // the 24 h backoff LinkedIn's own 429/999 put us in. While that pause is
  // still running, resuming crosses a confirm; once it has lapsed there is
  // nothing left to warn about, so the click fires straight through.
  const pausedUntilMs = session.paused_until ? new Date(session.paused_until).getTime() : NaN;
  const pauseActive = !Number.isNaN(pausedUntilMs) && pausedUntilMs > Date.now();

  return (
    <div className="rounded-lg border border-border bg-surface-2 p-3" data-testid="linkedin-session-section">
      <button
        type="button"
        onClick={() => setOpenOverride(!open)}
        aria-expanded={open}
        data-testid="linkedin-session-toggle"
        className="flex w-full items-center gap-2 text-left"
      >
        <span className="w-3 text-ink-4">{open ? "▾" : "▸"}</span>
        <span className="text-[12px] font-semibold text-ink-3">
          {t("settingsPage.session.title")}
        </span>
        <span
          data-testid="linkedin-status-pill"
          className={
            "ml-auto inline-flex h-[20px] items-center gap-[5px] rounded-full border px-2 text-[11px] font-medium " +
            pill.cls
          }
        >
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: pill.dot }} />
          {t(pill.label)}
        </span>
      </button>
      {open ? (
        <div className="mt-3 space-y-4">
          <p className="text-[12.5px] text-ink-3">
            <Trans i18nKey="settingsPage.session.intro" components={{ strong: <strong /> }} />
            <InfoDot label={t("settingsPage.session.howLabel")}>
              {t("settingsPage.session.howInfo")}
            </InfoDot>
          </p>

          {/* Status details + actions */}
          <div className="rounded-lg border border-border bg-surface p-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[12px] text-ink-3">
              <dt>{t("settingsPage.session.connectedAs")}</dt>
              <dd className="text-ink-2" data-testid="linkedin-connected-as">
                {session.connected_as || "—"}
              </dd>
              <dt>{t("settingsPage.session.expires")}</dt>
              <dd>{fmtDate(session.li_at_expires_at)}</dd>
              <dt>{t("settingsPage.session.lastValidated")}</dt>
              <dd>{fmtDate(session.last_validated_at)}</dd>
            </dl>

            {status === "backing_off" && (
              <div
                className="mt-3 rounded-md border border-bad-2 bg-bad-wash p-2.5 text-[11.5px] text-bad"
                data-testid="linkedin-backoff-notice"
              >
                {session.paused_reason
                  ? t("settingsPage.session.backoffNoticeReason", {
                      reason: session.paused_reason,
                    })
                  : t("settingsPage.session.backoffNotice")}
              </div>
            )}

            <div className="mt-3 flex flex-wrap gap-2">
              {!connected && !connecting && (
                <button
                  data-testid="linkedin-connect-btn"
                  onClick={() => connect.mutate()}
                  className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
                >
                  {t("settingsPage.session.connect")}
                </button>
              )}
              {connecting && (
                <span
                  data-testid="linkedin-connecting-hint"
                  className={"inline-flex h-[30px] items-center rounded-md px-3 text-[12px] font-medium " + MUTED_WARN_BOX}
                >
                  {t("settingsPage.session.connectingHint")}
                </span>
              )}
              {connected && (
                <button
                  data-testid="linkedin-validate-btn"
                  onClick={() => validate.mutate()}
                  disabled={validate.isPending}
                  className="inline-flex h-[30px] items-center rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink hover:bg-surface-3 disabled:opacity-60"
                >
                  {validate.isPending
                    ? t("settingsPage.session.validating")
                    : t("settingsPage.session.validate")}
                </button>
              )}
              {validate.isSuccess && !validate.isPending ? (
                <span
                  data-testid="linkedin-validate-result"
                  className="inline-flex h-[30px] items-center text-[12px] text-good"
                >
                  {t("settingsPage.session.validateOk")}
                </span>
              ) : null}
              {validate.isError ? (
                <span
                  data-testid="linkedin-validate-error"
                  className="inline-flex h-[30px] items-center text-[12px] text-bad"
                >
                  {t("settingsPage.session.validateFailed", {
                    message:
                      validate.error instanceof Error
                        ? validate.error.message
                        : t("settingsPage.session.errorFallback"),
                  })}
                </span>
              ) : null}
              {status === "backing_off" && (
                <button
                  data-testid="linkedin-resume-btn"
                  onClick={() => (pauseActive ? setResumeConfirm(true) : resume.mutate())}
                  className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
                >
                  {t("settingsPage.session.resume")}
                </button>
              )}
              {(connected || status === "expired" || status === "backing_off") && (
                <button
                  data-testid="linkedin-disconnect-btn"
                  onClick={() => disconnect.mutate()}
                  className="inline-flex h-[30px] items-center rounded-md border border-transparent px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
                >
                  {t("settingsPage.session.disconnect")}
                </button>
              )}
            </div>
          </div>

          {/* Membership + risk% + per-cap overrides now live in their own
              section (LinkedInRateLimitsSection), since they govern BOTH
              Referral Outreach and job-search caps. */}
        </div>
      ) : null}
      {resumeConfirm ? (
        <ConfirmDialog
          title={t("settingsPage.session.resumeConfirmTitle")}
          body={t("settingsPage.session.resumeConfirmBody", {
            until: fmtDate(session.paused_until),
          })}
          confirmLabel={t("settingsPage.session.resumeConfirmOk")}
          onConfirm={() => {
            setResumeConfirm(false);
            resume.mutate();
          }}
          onCancel={() => setResumeConfirm(false)}
        />
      ) : null}
    </div>
  );
});

// The experimental gate around LinkedIn job search — the SAME consent scaffold
// as Referral Outreach (hazard badge + ToS risk line + ack + Enable toggle),
// shared as `LinkedInOptIn` since 2026-08-02 (duplication audit D-F3), with its
// OWN opt-in but the SAME shared LinkedIn session (connect once, stays until
// Disconnect). `ack` is local state here on purpose: it resets whenever the
// Discover pane unmounts, so consent is re-given per visit.
// Memoized: `settings` is the query-stable object, `patch` a root useCallback.
export const LinkedInJobSearchSection = memo(function LinkedInJobSearchSection({
  settings,
  patch,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  const { data: session } = useLinkedInSession();
  const [ack, setAck] = useState(false);
  const enabled = settings.linkedin_search_enabled;
  const connected = session?.status === "valid";
  return (
    <LinkedInOptIn
      title={t("settingsPage.linkedinSearch.title")}
      intro={t("settingsPage.linkedinSearch.intro")}
      howLabel={t("settingsPage.linkedinSearch.howLabel")}
      howInfo={t("settingsPage.linkedinSearch.howInfo")}
      warning={t(JOB_SEARCH_WARNING)}
      ackLabel={t("settingsPage.linkedinSearch.ack")}
      ackTestid="linkedin-search-ack"
      ack={ack}
      onAck={setAck}
      enableLabel={t("settingsPage.linkedinSearch.enable")}
      enabled={enabled}
      onEnable={(ackAt) =>
        patch({ linkedin_search_enabled: true, linkedin_search_ack_at: ackAt })
      }
      onDisable={() => patch({ linkedin_search_enabled: false })}
      toggleTestid="linkedin-search-toggle"
      ackAt={settings.linkedin_search_ack_at}
      ackAtTestid="linkedin-search-ack-at"
    >
      <div className="space-y-3">
        {/* Same collapsible session as Referral Outreach — connect/disconnect
            here or there, it's one shared session. */}
        <LinkedInSessionSection />
        {connected ? (
          <LinkedInJobSearchBlock />
        ) : (
          <p className="text-[11.5px] text-ink-4">
            {t("settingsPage.linkedinSearch.connectHint")}
          </p>
        )}
      </div>
    </LinkedInOptIn>
  );
});

function LinkedInJobSearchBlock() {
  const { t } = useTranslation();
  const { data: session } = useLinkedInSession();
  const search = useLinkedinSearch();
  // The hourly job-search budget is our own throttle (pages/hour). When it's
  // spent, neither Fresh nor Next can fetch — reflect that in the UI (the
  // server enforces it too, returning 429).
  const hourRemaining = session?.rate_limits?.job_search_hour_remaining ?? 1;
  const throttled = hourRemaining <= 0;
  // The Next-page button shows only while the last Fresh search is continuable
  // (younger than the 12 h window, not at LinkedIn's end of results) AND the
  // hourly budget allows another page. The server enforces the same (409/429).
  const nextAvailable = Boolean(session?.search_cursor?.next_page_available) && !throttled;
  const busy = search.isPending;
  return (
    <div className="space-y-3" data-testid="linkedin-jobsearch-block">
      <div className="flex items-center gap-3">
        <div className="flex-1 text-[12.5px] text-ink-2">
          {t("settingsPage.linkedinSearch.runNow")}
        </div>
        <button
          data-testid="linkedin-jobsearch-btn"
          onClick={() => search.mutate("fresh")}
          disabled={busy || throttled}
          className="inline-flex h-[30px] shrink-0 items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:opacity-60"
        >
          {t("settingsPage.linkedinSearch.freshBtn")}
        </button>
      </div>
      {/* Scanning indicator (like the Discover scan pill) while a search runs —
          both buttons are disabled above until it resolves. */}
      {busy ? (
        <div
          className="flex items-center gap-2 text-[12px] italic text-ink-3"
          data-testid="linkedin-jobsearch-scanning"
        >
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
          {t("settingsPage.linkedinSearch.scanning")}
        </div>
      ) : null}
      {nextAvailable ? (
        <div className="flex items-center gap-3">
          <div className="flex-1 text-[12.5px] text-ink-2">
            {t("settingsPage.linkedinSearch.nextHint")}
            <InfoDot label={t("settingsPage.linkedinSearch.nextBtn")}>
              {t("settingsPage.linkedinSearch.nextInfo")}
            </InfoDot>
          </div>
          <button
            data-testid="linkedin-jobsearch-next-btn"
            onClick={() => search.mutate("next")}
            disabled={busy}
            className="inline-flex h-[30px] shrink-0 items-center rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink hover:bg-surface-3 disabled:opacity-60"
          >
            {t("settingsPage.linkedinSearch.nextBtn")}
          </button>
        </div>
      ) : null}
      {throttled ? (
        <div className="text-[11.5px] text-warn" data-testid="linkedin-jobsearch-throttled">
          {t("settingsPage.linkedinSearch.hourlyReached")}
        </div>
      ) : null}
      {/* Not a selector any more (2026-08-01). The request carries `count=25`
          whatever number a caller asks for, so a smaller setting never made a
          smaller request — it only threw away rows already fetched, while
          implying a lighter footprint. One page, stated plainly. */}
      <div className="flex items-center gap-3">
        <div className="flex-1 text-[12.5px] text-ink-2">
          {t("settingsPage.linkedinSearch.resultsPerSearch")}
          <InfoDot label={t("settingsPage.linkedinSearch.resultsPerSearch")}>
            <Trans
              i18nKey="settingsPage.linkedinSearch.resultsPerSearchInfo"
              components={{ strong: <strong /> }}
            />
          </InfoDot>
        </div>
        <span
          data-testid="linkedin-jobsearch-limit"
          className="text-[12px] font-medium text-ink-2"
        >
          {t("settingsPage.linkedinSearch.onePage")}
        </span>
      </div>
      {search.isSuccess ? (
        <div className="text-[11.5px] text-good" data-testid="linkedin-jobsearch-started">
          {t("settingsPage.linkedinSearch.started")}
        </div>
      ) : null}
      {search.isError ? (
        <div className="text-[11.5px] text-bad" data-testid="linkedin-jobsearch-error">
          {search.error instanceof Error
            ? search.error.message
            : t("settingsPage.linkedinSearch.failed")}
        </div>
      ) : null}
    </div>
  );
}

// The selectable values for one cap: every value up to the estimated ceiling,
// so the user can never pick above the max (the whole point of a dropdown over a
// free-text box). Fine-grained for small ceilings (every integer ≤ 20), stepped
// for larger ones so the list stays usable; the ceiling and the current value
// are always present so both are selectable.
function capOptions(ceiling: number, current: number): number[] {
  const step = ceiling <= 20 ? 1 : ceiling <= 60 ? 5 : ceiling <= 500 ? 25 : 50;
  const set = new Set<number>([1, ceiling, current]);
  for (let v = step; v < ceiling; v += step) set.add(v);
  // `current` is always kept (even a legacy 0) so the select's value is present.
  return [...set].filter((v) => v === current || (v >= 1 && v <= ceiling)).sort((a, b) => a - b);
}

// One override control: a dropdown bounded at the estimated ceiling. Selecting a
// value commits it immediately; the server recomputes and the query update flows
// the new caps back. Bounded options mean an override can never exceed the max
// (the server clamps too, as defence in depth).
function OverrideSelect({
  cap,
  onCommit,
  disabled,
}: {
  cap: LinkedInCap;
  onCommit: (value: number) => void;
  disabled: boolean;
}) {
  const options = capOptions(cap.ceiling, cap.effective);
  return (
    <select
      value={cap.effective}
      disabled={disabled}
      data-testid={`linkedin-cap-select-${cap.key}`}
      onChange={(e) => {
        const n = Number(e.target.value);
        if (Number.isFinite(n) && n !== cap.effective) onCommit(n);
      }}
      className="w-[72px] rounded-md border border-border bg-surface px-2 py-1 text-right text-[12.5px] text-ink disabled:opacity-60"
    >
      {options.map((v) => (
        <option key={v} value={v}>
          {v}
        </option>
      ))}
    </select>
  );
}

// LinkedIn self-imposed rate limits (maintainer directive 2026-08-01). One
// membership dropdown + one risk slider drive every cap; each cap is
// independently overridable. Changing membership OR risk resets overrides
// (enforced server-side). Lives in the Networking category beside the shared
// LinkedIn session it scopes to (maintainer 2026-08-02); it also carries the
// Discover-jobs search throttle (pages/hour), which links here by name.
export const LinkedInRateLimitsSection = memo(function LinkedInRateLimitsSection() {
  const { t } = useTranslation();
  const { data: session } = useLinkedInSession();
  const { data: settings } = useSettings();
  const setLimits = useSetLinkedInRateLimits();
  const rl = session?.rate_limits ?? null;
  // Caps only govern the two LinkedIn opt-ins, so with both off the card is
  // pure noise (maintainer 2026-08-02) — same "any feature" condition the
  // sidecar gates the session routes on.
  const anyLinkedInFeatureOn =
    Boolean(session?.enabled) || Boolean(settings?.linkedin_search_enabled);
  // Local slider draft for smooth dragging; committed on release. Re-seeds
  // whenever the server value changes (incl. the clamp on an out-of-range value).
  const [riskDraft, setRiskDraft] = useState<number | null>(null);
  const serverRisk = rl?.risk_pct ?? 60;
  const risk = riskDraft ?? serverRisk;
  const busy = setLimits.isPending;
  // Once the server's committed risk changes (incl. a clamp), drop the local
  // drag draft so the slider follows the authoritative value.
  useEffect(() => setRiskDraft(null), [serverRisk]);
  // Commit only a real change: pointer-up and blur can both fire for one
  // adjustment, and arrow-key changes commit on blur rather than per keypress.
  const commitRisk = (value: number) => {
    if (value !== serverRisk) setLimits.mutate({ risk_pct: value });
  };
  if (!rl || !anyLinkedInFeatureOn) return null;

  return (
    <Section title={t("settingsPage.rateLimits.title")}>
      <div className="space-y-4">
        <p className="text-[12.5px] text-ink-2">
          {t("settingsPage.rateLimits.intro")}
          <InfoDot label={t("settingsPage.rateLimits.title")}>
            {t("settingsPage.rateLimits.info")}
          </InfoDot>
        </p>

        {/* Membership type — the estimated-ceiling basis. */}
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="text-[13px] font-medium text-ink">
              {t("settingsPage.rateLimits.membershipLabel")}
            </div>
            <div className="text-[12px] text-ink-3">
              {t("settingsPage.rateLimits.membershipHint")}
            </div>
          </div>
          <select
            data-testid="linkedin-membership-select"
            value={rl.membership_type}
            disabled={busy}
            onChange={(e) => setLimits.mutate({ membership_type: e.target.value })}
            className="rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink disabled:opacity-60"
          >
            {rl.memberships.map((m) => (
              <option key={m} value={m}>
                {t(`settingsPage.rateLimits.membership.${m}`)}
              </option>
            ))}
          </select>
        </div>

        {/* Risk appetite — scales the ceilings. 100% = at the estimated limit. */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <div className="text-[13px] font-medium text-ink">
              {t("settingsPage.rateLimits.riskLabel")}
            </div>
            <div
              className="text-[12.5px] font-medium tabular-nums text-ink-2"
              data-testid="linkedin-risk-value"
            >
              {risk}%
            </div>
          </div>
          <input
            type="range"
            min={10}
            max={100}
            step={5}
            value={risk}
            disabled={busy}
            data-testid="linkedin-risk-slider"
            onChange={(e) => setRiskDraft(Number(e.target.value))}
            onPointerUp={(e) => commitRisk(Number((e.target as HTMLInputElement).value))}
            onBlur={(e) => commitRisk(Number(e.target.value))}
            className="w-full accent-accent disabled:opacity-60"
          />
          <div className="text-[11.5px] text-warn" data-testid="linkedin-risk-warn">
            {t("settingsPage.rateLimits.riskWarn")}
          </div>
        </div>

        {/* Per-meter overrides — each pre-filled with the effective cap. */}
        <div className="space-y-2">
          <div className="flex items-center text-[13px] font-medium text-ink">
            {t("settingsPage.rateLimits.capsLabel")}
            <InfoDot label={t("settingsPage.rateLimits.capsLabel")}>
              {t("settingsPage.rateLimits.capsInfo")}
            </InfoDot>
          </div>
          <div className="divide-y divide-border-2 rounded-md border border-border-2">
            {rl.caps.map((cap) => (
              <div key={cap.key} className="flex items-center gap-3 px-3 py-2">
                <div className="flex-1">
                  <div className="text-[12.5px] text-ink">
                    {t(`settingsPage.rateLimits.cap.${cap.key}`, { defaultValue: cap.label })}
                  </div>
                  <div className="text-[11px] text-ink-4">
                    {t("settingsPage.rateLimits.ofCeiling", { ceiling: cap.ceiling })}
                    {cap.overridden ? ` · ${t("settingsPage.rateLimits.custom")}` : ""}
                  </div>
                </div>
                <OverrideSelect
                  cap={cap}
                  disabled={busy}
                  onCommit={(value) =>
                    setLimits.mutate({ override_key: cap.key, override_value: value })
                  }
                />
              </div>
            ))}
          </div>
          <button
            data-testid="linkedin-caps-reset-btn"
            disabled={busy}
            onClick={() => setLimits.mutate({ reset_overrides: true })}
            className="inline-flex h-[28px] items-center rounded-md border border-border-2 bg-surface px-2.5 text-[11.5px] font-medium text-ink-2 hover:bg-surface-3 disabled:opacity-60"
          >
            {t("settingsPage.rateLimits.resetBtn")}
          </button>
        </div>
      </div>
    </Section>
  );
});
