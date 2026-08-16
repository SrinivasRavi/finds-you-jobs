// About & Updates pane — version, in-app software update, support (GitHub
// Sponsors), community (Discord + #prompts-and-configs), and the AGPL/source
// links. The update controls drive tauri-plugin-updater via shell/updater.ts
// and degrade to an "unavailable" note in the browser-dev path.

import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Icon } from "../../shell/icons";
import { useAppVersion } from "../../shell/appVersion";
import { openExternal } from "../../shell/openExternal";
import { checkForUpdate, updaterAvailable, useAutoUpdateCheck } from "../../shell/updater";
import { Section, Toggle } from "./shared";

// Outbound links. Kept here (and mirrored in README.md / the marketing site for
// the Discord invite) so there's one obvious place to change them.
const SPONSOR_URL = "https://github.com/sponsors/SrinivasRavi";
const DISCORD_URL = "https://discord.gg/YsMxkwu7SY";
const REPO_URL = "https://github.com/SrinivasRavi/finds-you-jobs";
const ISSUES_URL = "https://github.com/SrinivasRavi/finds-you-jobs/issues";

type UpdateState =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "uptodate" }
  | { kind: "available"; version: string }
  | { kind: "downloading"; percent: number | null }
  | { kind: "error" };

function LinkButton({
  onClick,
  icon,
  children,
  testid,
  primary,
}: {
  onClick: () => void;
  icon: "download" | "heart" | "share" | "externalLink" | "file";
  children: React.ReactNode;
  testid: string;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      data-testid={testid}
      onClick={onClick}
      className={
        "inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-[12.5px] font-medium transition-colors " +
        (primary
          ? "bg-accent text-white hover:bg-accent/90"
          : "border border-border bg-surface text-ink-2 hover:bg-surface-3")
      }
    >
      <Icon name={icon} size={14} strokeWidth={2} />
      {children}
    </button>
  );
}

export const AboutSection = memo(function AboutSection() {
  const { t } = useTranslation();
  const version = useAppVersion();
  const [autoCheck, setAutoCheck] = useAutoUpdateCheck();
  const [state, setState] = useState<UpdateState>({ kind: "idle" });
  const canUpdate = updaterAvailable();

  async function onCheck() {
    setState({ kind: "checking" });
    try {
      const result = await checkForUpdate();
      setState(
        result.available
          ? { kind: "available", version: result.version }
          : { kind: "uptodate" },
      );
    } catch {
      setState({ kind: "error" });
    }
  }

  async function onInstall() {
    setState({ kind: "downloading", percent: null });
    try {
      const result = await checkForUpdate();
      if (!result.available) {
        setState({ kind: "uptodate" });
        return;
      }
      await result.install((fraction) =>
        setState({ kind: "downloading", percent: fraction == null ? null : Math.round(fraction * 100) }),
      );
      // On success the app relaunches; this line rarely runs.
    } catch {
      setState({ kind: "error" });
    }
  }

  return (
    <>
      {/* Version + software update */}
      <Section title={t("settingsPage.about.updatesTitle")}>
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <div className="text-[13px] font-medium text-ink">{t("settingsPage.about.currentVersion")}</div>
              <div className="text-[13px] text-ink-2" data-testid="about-version">
                {version ?? "…"}
              </div>
            </div>
            {canUpdate ? (
              <button
                type="button"
                data-testid="about-check-updates"
                disabled={state.kind === "checking" || state.kind === "downloading"}
                onClick={onCheck}
                className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-[12.5px] font-medium text-ink-2 transition-colors hover:bg-surface-3 disabled:opacity-50"
              >
                <Icon name="download" size={14} strokeWidth={2} />
                {t("settingsPage.about.checkButton")}
              </button>
            ) : (
              <span className="text-[11.5px] text-ink-4" data-testid="about-update-unavailable">
                {t("settingsPage.about.unavailableInBrowser")}
              </span>
            )}
          </div>

          {/* Update status line */}
          {canUpdate && state.kind !== "idle" ? (
            <div className="text-[12px]" data-testid="about-update-status">
              {state.kind === "checking" ? (
                <span className="text-ink-3">{t("settingsPage.about.checking")}</span>
              ) : state.kind === "uptodate" ? (
                <span className="text-ink-3">{t("settingsPage.about.upToDate")}</span>
              ) : state.kind === "error" ? (
                <span className="text-bad">{t("settingsPage.about.checkError")}</span>
              ) : state.kind === "downloading" ? (
                <span className="text-ink-3">
                  {state.percent == null
                    ? t("settingsPage.about.downloadingIndeterminate")
                    : t("settingsPage.about.downloading", { percent: state.percent })}
                </span>
              ) : state.kind === "available" ? (
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-ink-2">
                    {t("settingsPage.about.updateAvailable", { version: state.version })}
                  </span>
                  <LinkButton
                    testid="about-install-update"
                    icon="download"
                    primary
                    onClick={onInstall}
                  >
                    {t("settingsPage.about.downloadInstall")}
                  </LinkButton>
                  <span className="text-[11px] text-ink-4">{t("settingsPage.about.restartNote")}</span>
                </div>
              ) : null}
            </div>
          ) : null}

          {/* Check-on-launch preference */}
          <div className="flex items-start gap-3 border-t border-border pt-3">
            <div className="flex-1">
              <div className="text-[13px] font-medium text-ink">{t("settingsPage.about.autoCheckLabel")}</div>
              <div className="text-[11.5px] text-ink-3">{t("settingsPage.about.autoCheckHint")}</div>
            </div>
            <Toggle testid="about-auto-check-toggle" on={autoCheck} onChange={setAutoCheck} />
          </div>

          {/* Data-preservation reassurance */}
          <div className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-[11.5px] leading-relaxed text-ink-3">
            {t("settingsPage.about.dataSafe")}
          </div>
        </div>
      </Section>

      {/* Support */}
      <Section title={t("settingsPage.about.supportTitle")}>
        <div className="space-y-3">
          <p className="text-[12.5px] leading-relaxed text-ink-2">{t("settingsPage.about.supportBody")}</p>
          <LinkButton testid="about-sponsor-link" icon="heart" onClick={() => openExternal(SPONSOR_URL)}>
            {t("settingsPage.about.sponsorButton")}
          </LinkButton>
        </div>
      </Section>

      {/* Community */}
      <Section title={t("settingsPage.about.communityTitle")}>
        <div className="space-y-3">
          <p className="text-[12.5px] leading-relaxed text-ink-2">{t("settingsPage.about.communityBody")}</p>
          <LinkButton testid="about-discord-link" icon="share" onClick={() => openExternal(DISCORD_URL)}>
            {t("settingsPage.about.discordButton")}
          </LinkButton>
        </div>
      </Section>

      {/* About / legal */}
      <Section title={t("settingsPage.about.aboutTitle")}>
        <div className="space-y-3">
          <p className="text-[12.5px] leading-relaxed text-ink-2">{t("settingsPage.about.licenseLine")}</p>
          <div className="flex flex-wrap gap-2">
            <LinkButton testid="about-repo-link" icon="externalLink" onClick={() => openExternal(REPO_URL)}>
              {t("settingsPage.about.sourceButton")}
            </LinkButton>
            <LinkButton testid="about-issues-link" icon="file" onClick={() => openExternal(ISSUES_URL)}>
              {t("settingsPage.about.issuesButton")}
            </LinkButton>
          </div>
        </div>
      </Section>
    </>
  );
});
