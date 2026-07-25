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

import { memo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import {
  useConnectLinkedIn,
  useDisconnectLinkedIn,
  useLinkedinSearch,
  useLinkedInSession,
  useResumeLinkedIn,
  useSetLinkedInTier,
  useSettings,
  useUpdateSettings,
  useValidateLinkedIn,
} from "../../api/queries";
import type { LinkedInSessionState, Settings as SettingsT } from "../../api/types";
import { InfoDot } from "../../shell/InfoDot";
import { ExperimentalHazard, LinkedInRiskLine, MUTED_WARN_BOX, Section, Toggle } from "./shared";

// LinkedIn Job Search breaks ToS by SCRAPING listings (not messaging) — its own
// justification: one-off + small default batch, so it reads as ordinary browsing.
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
  const setTier = useSetLinkedInTier();
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);

  if (!session) return null;
  const status = session.status;
  const pill = statusPill(status);
  const connecting = status === "connecting" || connect.isPending;
  const connected = status === "valid";
  const open = openOverride ?? !connected; // expanded until connected, then tidy

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
                  onClick={() => resume.mutate()}
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

          {/* Account tier (US-REF-08 / US-NW-10) */}
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <div className="flex items-center text-[13px] font-medium text-ink">
                {t("settingsPage.session.tier")}
                <InfoDot label={t("settingsPage.session.tierCapsLabel")}>
                  {t("settingsPage.session.tierInfo")}
                </InfoDot>
              </div>
              <div className="text-[12px] text-ink-3">{t("settingsPage.session.tierHint")}</div>
            </div>
            <select
              data-testid="linkedin-tier-select"
              value={session.account_tier}
              onChange={(e) => setTier.mutate(e.target.value as "new" | "seasoned")}
              className="rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink"
            >
              <option value="new">{t("settingsPage.session.tierNew")}</option>
              <option value="seasoned">{t("settingsPage.session.tierSeasoned")}</option>
            </select>
          </div>
        </div>
      ) : null}
    </div>
  );
});

// Server clamps to this range; keep the UI honest to it.
const LI_LIMIT_MIN = 25;
const LI_LIMIT_MAX = 250;

// The experimental gate around LinkedIn job search — mirrors Referral Outreach
// (hazard badge + ToS risk line + ack + Enable toggle), with its OWN opt-in but
// the SAME shared LinkedIn session (connect once, stays until Disconnect).
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
    <Section title={t("settingsPage.linkedinSearch.title")} titleExtra={<ExperimentalHazard />}>
      <div className="space-y-3">
        <p className="text-[12.5px] text-ink-2">
          {t("settingsPage.linkedinSearch.intro")}
          <InfoDot label={t("settingsPage.linkedinSearch.howLabel")}>
            {t("settingsPage.linkedinSearch.howInfo")}
          </InfoDot>
        </p>
        <LinkedInRiskLine detail={t(JOB_SEARCH_WARNING)} />
        <label className="flex items-start gap-2 text-[12px] font-medium text-ink-2">
          <input
            type="checkbox"
            checked={ack || enabled}
            onChange={(e) => setAck(e.target.checked)}
            data-testid="linkedin-search-ack"
            className="mt-0.5"
          />
          {t("settingsPage.linkedinSearch.ack")}
        </label>
        <div className="flex items-center gap-3">
          <div className="flex-1 text-[13px] font-medium text-ink">
            {t("settingsPage.linkedinSearch.enable")}
          </div>
          <Toggle
            on={enabled}
            onChange={(v) => {
              if (v && !ack) return;
              patch(
                v
                  ? { linkedin_search_enabled: v, linkedin_search_ack_at: new Date().toISOString() }
                  : { linkedin_search_enabled: v },
              );
              if (!v) setAck(false);
            }}
            testid="linkedin-search-toggle"
          />
        </div>
        {settings.linkedin_search_ack_at ? (
          <div className="text-[11px] text-ink-4" data-testid="linkedin-search-ack-at">
            {t("settingsPage.acknowledgedOn", {
              date: new Date(settings.linkedin_search_ack_at).toLocaleDateString(),
            })}
          </div>
        ) : null}
        {enabled ? (
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
        ) : null}
      </div>
    </Section>
  );
});

function LinkedInJobSearchBlock() {
  const { t } = useTranslation();
  const search = useLinkedinSearch();
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const limit = settings?.linkedin_search_limit ?? 50;
  const onChangeLimit = (v: number) => update.mutate({ linkedin_search_limit: v });
  return (
    <div className="space-y-3" data-testid="linkedin-jobsearch-block">
      <div className="flex items-center gap-3">
        <div className="flex-1 text-[12.5px] text-ink-2">
          {t("settingsPage.linkedinSearch.runNow")}
        </div>
        <button
          data-testid="linkedin-jobsearch-btn"
          onClick={() => search.mutate(limit)}
          disabled={search.isPending}
          className="inline-flex h-[30px] shrink-0 items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:opacity-60"
        >
          {search.isPending
            ? t("settingsPage.linkedinSearch.searching")
            : t("settingsPage.linkedinSearch.searchBtn")}
        </button>
      </div>
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
        <select
          value={String(limit)}
          data-testid="linkedin-jobsearch-limit"
          onChange={(e) => onChangeLimit(Number(e.target.value))}
          className="h-[30px] rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink"
        >
          {Array.from(
            { length: (LI_LIMIT_MAX - LI_LIMIT_MIN) / 25 + 1 },
            (_, i) => LI_LIMIT_MIN + i * 25,
          ).map((n) => (
            <option key={n} value={n}>
              {t("settingsPage.linkedinSearch.jobsOption", { n })}
            </option>
          ))}
        </select>
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
