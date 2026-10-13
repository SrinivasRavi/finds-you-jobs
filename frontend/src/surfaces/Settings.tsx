// Settings (US-SET / section 13): the category rail and pane composition; the
// pane sections live in ./settings/* (F-M6).

import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSettings, useUpdateSettings } from "../api/queries";
import type { Settings as SettingsT } from "../api/types";
import { AboutSection } from "./settings/AboutSection";
import { AIProvidersPanel } from "./settings/AIProvidersPanel";
import { ApplicantDetailsForm } from "./settings/ApplicantDetails";
import { AppearanceSection } from "./settings/AppearanceSection";
import { AutomationSection } from "./settings/AutomationSection";
import { DiscoveryKeysSection, DiscoverySourcesSection } from "./settings/DiscoverySources";
import { FollowedCompaniesSection } from "./settings/FollowedCompaniesSection";
import { LifecycleSection } from "./settings/LifecycleSection";
import { LinkedInJobSearchSection, LinkedInRateLimitsSection } from "./settings/LinkedInSections";
import { ObservabilitySection } from "./settings/ObservabilitySection";
import { ModelPicker } from "./settings/ModelPicker";
import { PromptCard } from "./settings/PromptsSection";
import { ReferralOutreachSection } from "./settings/ReferralOutreachSection";
import { ScoringSection } from "./settings/ScoringSection";
import { SETTINGS_CATS, SettingsNav, type SettingsCat } from "./settings/SettingsNav";
import { Section } from "./settings/shared";

export function Settings() {
  const { t } = useTranslation();
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const [cat, setCat] = useState<SettingsCat>("providers");
  const [ack, setAck] = useState(false);

  // Stable callbacks (update.mutate is referentially stable in TanStack Query
  // v5) so the memoized pane sections only re-render when `settings` changes.
  const updateMutate = update.mutate;
  const patch = useCallback(
    (p: Partial<SettingsT>) => {
      updateMutate(p);
    },
    [updateMutate],
  );

  if (!settings) return null;

  const active = SETTINGS_CATS.find((c) => c.id === cat) ?? SETTINGS_CATS[0];
  return (
    <>
      <header className="flex min-h-[48px] items-center border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">{t("nav.settings")}</h1>
      </header>
      <main className="flex min-h-0 flex-1 overflow-hidden">
        <SettingsNav active={cat} onPick={setCat} />
        <div className="flex-1 overflow-y-auto">
        {/* All panes share one comfortable width (2026-07-23: the full-width
            Prompts pane read as too spread out). Less side-padding than before. */}
        <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-5">
          <header className="space-y-1">
            <h2 className="text-[18px] font-semibold text-ink">{t(active.label)}</h2>
            <p className="text-[13px] text-ink-3">{t(active.blurb)}</p>
          </header>

          {cat === "discovery" && (
          <div className="space-y-8">
          {/* Discovery sources — per-family opt-out toggles (2026-07-18).
              First section on purpose: discovery is the first stage of the
              pipeline everything below feeds on. */}
          <Section title={t("settingsPage.sources.title")}>
            <DiscoverySourcesSection />
          </Section>

          {/* Boards the user follows: rows of the same sources list
              (watch-company.md section 3). */}
          <Section title={t("jobBoard.prefs.tracked.heading")}>
            <FollowedCompaniesSection />
          </Section>

          {/* BYO scraper keys — their own card so the sources list above stays
              a pure pick-list (maintainer 2026-07-23). */}
          <Section title={t("settingsPage.sources.keys.title")}>
            <DiscoveryKeysSection />
          </Section>

          {/* LinkedIn job search sits above Scoring (maintainer 2026-07-23) — an
              experimental discovery source with its own ToS opt-in, sharing the
              LinkedIn session with Referral Outreach. */}
          <LinkedInJobSearchSection settings={settings} patch={patch} />

          </div>
          )}

          {/* Scoring (url-less-jobs.md section 11.5): the method menu, the user
              weights, the batch cap, AND the rubric the AI method follows with
              the model that runs it. The Scoring pane used to point at another
              pane for its own rubric, which is the information architecture
              filing a bug report. */}
          {cat === "scoring" && (
          <div className="space-y-8">
          <ScoringSection
            settings={settings}
            patch={patch}
            onAddKey={() => setCat("providers")}
          />
          <Section title={t("settingsPage.prompts.titleScoreModel")}>
            <ModelPicker
              kind="score"
              settings={settings}
              patch={patch}
              hint={t("settingsPage.prompts.hintScoreModel")}
            />
          </Section>
          <Section title={t("scoreMethod.llm")}>
            <PromptCard kind="score" />
          </Section>
          </div>
          )}

          {/* Resume & cover letter — 1 category, 2 cards (maintainer,
              2026-10-08: "I am ok with resume tailoring and cover letter
              sharing the same settings option"). The 2 generate-on-save
              toggles are a pair and would otherwise have been split across
              2 categories holding 1 control each. */}
          {cat === "documents" && (
          <div className="space-y-8">
          <AutomationSection settings={settings} patch={patch} />
          <Section title={t("settingsPage.prompts.titleTailor")}>
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="text-[13px] font-medium text-ink">
                    {t("settingsPage.applicant.resumeLocation")}
                  </div>
                  <div className="text-[12px] text-ink-3">
                    {t("settingsPage.applicant.resumeLocationHint")}
                  </div>
                </div>
                <select
                  data-testid="resume-location-select"
                  value={settings.resume_location}
                  onChange={(e) =>
                    patch({ resume_location: e.target.value as SettingsT["resume_location"] })
                  }
                  className="h-[30px] rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink"
                >
                  <option value="posting">{t("settingsPage.applicant.rl_posting")}</option>
                  <option value="master">{t("settingsPage.applicant.rl_master")}</option>
                  <option value="none">{t("settingsPage.applicant.rl_none")}</option>
                </select>
              </div>
              <ModelPicker kind="tailor" settings={settings} patch={patch} />
              <PromptCard kind="tailor" />
            </div>
          </Section>
          <Section title={t("settingsPage.prompts.titleCover")}>
            <div className="space-y-4">
              <ModelPicker kind="cover" settings={settings} patch={patch} />
              <PromptCard kind="cover" />
            </div>
          </Section>
          </div>
          )}

          {/* Applying — the Form answers prompt (what the master resume is
              read into) and the Applier's own model, which had no UI at all
              before today. Filed by what it produces rather than what it eats
              (url-less-jobs.md section 11.5). */}
          {cat === "applying" && (
          <div className="space-y-8">
          <Section title={t("settingsPage.applicant.title")}>
            <ApplicantDetailsForm />
          </Section>
          <Section title={t("settingsPage.prompts.titleApplyModel")}>
            <ModelPicker
              kind="apply"
              settings={settings}
              patch={patch}
              hint={t("settingsPage.prompts.hintApplyModel")}
            />
          </Section>
          <Section title={t("settingsPage.prompts.titleExtract")}>
            <div className="space-y-4">
              <ModelPicker kind="extract" settings={settings} patch={patch} />
              <PromptCard kind="extract" />
            </div>
          </Section>
          </div>
          )}

          {cat === "providers" && (
          <div className="space-y-8">
          {/* LLM Providers (FR-SET-06 / US-SET-07) — tile grid + Verify + Save */}
          <Section title={t("settingsPage.providers.title")}>
            <AIProvidersPanel settings={settings} />
          </Section>
          </div>
          )}

          {cat === "networking" && (
          <div className="space-y-8">

          {/* Referral Outreach risk toggle — the canonical feature name for the
              automated LinkedIn module (maintainer, 2026-07-10). The Networking
              tab (contact CRM + kanban + manual tracking) is ALWAYS available and
              carries no risk; this gates only the automated actions. This section
              is deliberately the feature's ONE reveal point (it is never
              advertised elsewhere), so the copy carries the full context. */}
          <ReferralOutreachSection settings={settings} patch={patch} ack={ack} onAck={setAck} />

          {/* LinkedIn self-imposed rate limits — beside the feature whose
              session it governs (maintainer 2026-08-02: feature configs live in
              their feature's category; only lifecycle + logs stay under Data).
              It also caps the Discover-jobs LinkedIn search (pages/hour) — that
              block links here by name. Renders nothing until a rate-limit
              profile exists. */}
          <LinkedInRateLimitsSection />

          {/* The referral message prompt, beside the feature that sends it.
              Its model is routed as the `draft` operation, which is why it had
              no picker while the pane was keyed on prompt names. */}
          <Section title={t("settingsPage.prompts.titleNetworkerDraft")}>
            <div className="space-y-4">
              <ModelPicker
                kind="draft"
                settings={settings}
                patch={patch}
                hint={t("settingsPage.prompts.hintDraftModel")}
              />
              <PromptCard kind="networker_draft" />
            </div>
          </Section>
          </div>
          )}

          {cat === "data" && (
          <div className="space-y-8">
          {/* Observability */}
          <ObservabilitySection settings={settings} patch={patch} />

          {/* Contact & data lifecycle (FR-SYS-06 / FR-NW-15) — configurable
              windows for kanban ghosting, purge, and feed aging. */}
          <LifecycleSection settings={settings} patch={patch} />
          </div>
          )}

          {cat === "appearance" && (
          <div className="space-y-8">
          <AppearanceSection />
          </div>
          )}

          {cat === "about" && (
          <div className="space-y-8">
          <AboutSection />
          </div>
          )}
        </div>
        </div>
      </main>
    </>
  );
}
