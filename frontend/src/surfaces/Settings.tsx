// Settings (US-SET / section 13) — Automation-on-Save, LLM providers + per-operation
// engine routing, the LinkedIn networking risk toggle w/ warning copy + ack,
// observability, appearance (theme). Ports settings*.html (product sections
// only — the prototype's purple "internal UI testing" mockups are not product).
//
// Split 2026-07-25 (F-M6): the pane sections live in ./settings/*; this file
// keeps the category rail state + the pane composition.

import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/index";
import { useSettings, useUpdateSettings } from "../api/queries";
import type { RescorePreview, Settings as SettingsT } from "../api/types";
import { RescoreAiDialog } from "../shell/RescoreAiDialog";
import { AboutSection } from "./settings/AboutSection";
import { AIProvidersPanel } from "./settings/AIProvidersPanel";
import { AppearanceSection } from "./settings/AppearanceSection";
import { AutomationSection } from "./settings/AutomationSection";
import { DiscoveryKeysSection, DiscoverySourcesSection } from "./settings/DiscoverySources";
import { LifecycleSection } from "./settings/LifecycleSection";
import { LinkedInJobSearchSection, LinkedInRateLimitsSection } from "./settings/LinkedInSections";
import { ObservabilitySection } from "./settings/ObservabilitySection";
import { EngineRoutingSection } from "./settings/PromptsSection";
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
  // Switching Scoring keyword → AI: the server never spends on its own, so
  // preview the cache misses and ask before any token goes out (maintainer
  // 2026-07-23). Jobs already AI-scored at the current resume are skipped.
  const [rescoreAsk, setRescoreAsk] = useState<RescorePreview | null>(null);

  // Stable callbacks (update.mutate is referentially stable in TanStack Query
  // v5) so the memoized pane sections only re-render when `settings` changes.
  const updateMutate = update.mutate;
  const patch = useCallback(
    (p: Partial<SettingsT>) => {
      updateMutate(p);
    },
    [updateMutate],
  );

  const scoringMode = settings?.scoring_mode;
  const pickScoringMode = useCallback(
    (mode: SettingsT["scoring_mode"]) => {
      const was = scoringMode;
      updateMutate(
        { scoring_mode: mode },
        {
          onSuccess: () => {
            if (mode === "llm" && was === "keyword") {
              void api.rescorePreview().then((preview) => {
                if (preview.to_score > 0) setRescoreAsk(preview);
              });
            }
          },
        },
      );
    },
    [scoringMode, updateMutate],
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

          {/* BYO scraper keys — their own card so the sources list above stays
              a pure pick-list (maintainer 2026-07-23). */}
          <Section title={t("settingsPage.sources.keys.title")}>
            <DiscoveryKeysSection />
          </Section>

          {/* LinkedIn job search sits above Scoring (maintainer 2026-07-23) — an
              experimental discovery source with its own ToS opt-in, sharing the
              LinkedIn session with Referral Outreach. */}
          <LinkedInJobSearchSection settings={settings} patch={patch} />

          {/* Scoring: a scanned job is scored before anything else happens to it.
              Every scanned job is scored; the choice is HOW. AI failures fall
              back to a grey keyword score (retry in Logs). */}
          <ScoringSection settings={settings} patch={patch} onPickMode={pickScoringMode} />

          {/* Automation on Save — split defaults (FR-SET-02): Resume ON, Cover ON.
              After Scoring in the workflow (maintainer 2026-07-23). */}
          <AutomationSection settings={settings} patch={patch} />

          </div>
          )}

          {/* The "Applications" settings category (P1 Applier statement +
              Application-profile editor) was removed 2026-07-23 (maintainer:
              no value in P1). The ApplicationProfileEditor component is parked
              below, unreferenced, for trivial restoration; the profile is still
              auto-extracted on master-save regardless. */}

          {cat === "providers" && (
          <div className="space-y-8">
          {/* LLM Providers (FR-SET-06 / US-SET-07) — tile grid + Verify + Save */}
          <Section title={t("settingsPage.providers.title")}>
            <AIProvidersPanel settings={settings} />
          </Section>
          </div>
          )}

          {cat === "prompts" && (
          <div className="space-y-8">
          {/* Per-operation engine routing + editable system prompts (FR-SET-11).
              Its own category so prompt editing is discoverable — the old long
              scroll buried it at the bottom (maintainer 2026-07-23). */}
          <Section title={t("settingsPage.prompts.title")}>
            <EngineRoutingSection settings={settings} patch={patch} />
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
      {rescoreAsk !== null ? (
        <RescoreAiDialog
          preview={rescoreAsk}
          reason="mode-switch"
          onClose={() => setRescoreAsk(null)}
        />
      ) : null}
    </>
  );
}
