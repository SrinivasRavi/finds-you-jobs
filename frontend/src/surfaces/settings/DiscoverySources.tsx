// Discovery sources + BYO scraper keys (extracted from Settings.tsx 2026-07-25,
// F-M6 monolith split — pure moves, zero behavior change). Both sections take
// no props (they read their own queries), so memo() is trivially honest: root
// Settings state changes (ack, rescore dialog) no longer re-render them.

import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useDeleteDiscoveryCredential,
  useDiscoveryCredentials,
  useDiscoverySources,
  useSaveDiscoveryCredential,
  useToggleDiscoverySource,
} from "../../api/queries";

// Discovery sources (maintainer directive 2026-07-18): one checkbox per
// scraper source family, all ON by default — pure opt-out. Lets a user drop a
// family that yields nothing for their role/location, and lets source efficacy
// be tested in isolation. Grouped by kind so the list of 18+ reads at a glance.
// Each section title carries its own master checkbox (2026-07-18 #5) that
// flips the whole section in one atomic POST; Apify actors are their own
// section (`sectionOf` routes their rows past the kind grouping).
const SOURCE_KIND_GROUPS: { kind: string; heading: string; blurb: string }[] = [
  {
    kind: "ats",
    heading: "settingsPage.sources.ats.heading",
    blurb: "settingsPage.sources.ats.blurb",
  },
  {
    kind: "board",
    heading: "settingsPage.sources.board.heading",
    blurb: "settingsPage.sources.board.blurb",
  },
  {
    kind: "search",
    heading: "settingsPage.sources.search.heading",
    blurb: "settingsPage.sources.search.blurb",
  },
  {
    kind: "apify",
    heading: "settingsPage.sources.apify.heading",
    blurb: "settingsPage.sources.apify.blurb",
  },
  {
    kind: "fallback",
    heading: "settingsPage.sources.fallback.heading",
    blurb: "settingsPage.sources.fallback.blurb",
  },
];

/** Which Settings section a catalog row belongs to. The Apify family row and
 *  its per-actor rows form their own section regardless of catalog kind. */
function sectionOf(s: { id: string; kind: string }): string {
  return s.id === "apify" || s.id.startsWith("apify:") ? "apify" : s.kind;
}

// BYO-key rows (Apify / Brave): a key input per provider, sealed at rest
// sidecar-side; saving the Apify key seeds its actor sources (Naukri/Indeed/
// Seek/LinkedIn deep-JD), saving Brave seeds the meta-search source.
function CredentialRow({ id, label, hint }: { id: string; label: string; hint: string }) {
  const { t } = useTranslation();
  const { data: creds } = useDiscoveryCredentials();
  const save = useSaveDiscoveryCredential();
  const remove = useDeleteDiscoveryCredential();
  const [draft, setDraft] = useState("");
  const row = creds?.find((c) => c.id === id);
  if (!row) return null;
  return (
    <div className="flex items-center gap-3" data-testid={`discovery-credential-${id}`}>
      <div className="flex-1">
        <div className="text-[13px] font-medium text-ink">{label}</div>
        <div className="text-[12px] text-ink-3">{hint}</div>
      </div>
      {row.has_key ? (
        <>
          <span className="font-mono text-[11px] text-ink-3">{row.key_hint ?? "•••"}</span>
          <button
            type="button"
            data-testid={`discovery-credential-remove-${id}`}
            onClick={() => remove.mutate(id)}
            className="rounded-md border border-border bg-surface px-2.5 py-1 text-[12px] text-ink-2 hover:border-border-2"
          >
            {t("settingsPage.sources.keys.remove")}
          </button>
        </>
      ) : (
        <>
          <input
            type="password"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={t("settingsPage.sources.keys.keyPlaceholder")}
            data-testid={`discovery-credential-input-${id}`}
            className="h-[30px] w-44 rounded-md border border-border-2 bg-surface px-2 text-[12px] text-ink placeholder:text-ink-4"
          />
          <button
            type="button"
            disabled={!draft.trim() || save.isPending}
            data-testid={`discovery-credential-save-${id}`}
            onClick={() =>
              save.mutate({ id, key: draft.trim() }, { onSuccess: () => setDraft("") })
            }
            className="rounded-md border border-accent bg-accent px-2.5 py-1 text-[12px] font-medium text-white hover:bg-accent-ink disabled:opacity-50"
          >
            {t("settingsPage.sources.keys.save")}
          </button>
        </>
      )}
    </div>
  );
}

/** The master checkbox in a section title — checked when every row in the
 *  section is on, unchecked when every row is off, indeterminate when mixed. */
function SectionMasterCheckbox({
  checked,
  indeterminate,
  onChange,
  testid,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: (enabled: boolean) => void;
  testid: string;
}) {
  const { t } = useTranslation();
  return (
    <input
      type="checkbox"
      checked={checked}
      ref={(el) => {
        if (el) el.indeterminate = indeterminate;
      }}
      onChange={(e) => onChange(e.target.checked)}
      data-testid={testid}
      title={t("settingsPage.sources.sectionToggleTitle")}
    />
  );
}

export const DiscoverySourcesSection = memo(function DiscoverySourcesSection() {
  const { t } = useTranslation();
  const { data: sources } = useDiscoverySources();
  const toggle = useToggleDiscoverySource();
  if (!sources) return null;
  return (
    <div className="space-y-4" data-testid="discovery-sources">
      {SOURCE_KIND_GROUPS.map(({ kind, heading, blurb }) => {
        const isApify = kind === "apify";
        const sectionRows = sources.filter((s) => sectionOf(s) === kind);
        // Apify: the family row IS the section master; list only actor rows.
        const rows = isApify ? sectionRows.filter((s) => s.id !== "apify") : sectionRows;
        const family = isApify ? sectionRows.find((s) => s.id === "apify") : undefined;
        if (sectionRows.length === 0) return null;
        const allOn = rows.length > 0 && rows.every((s) => s.enabled);
        const anyOn = rows.some((s) => s.enabled);
        const masterChecked = isApify ? Boolean(family?.enabled) : allOn;
        const masterMixed = isApify
          ? Boolean(family?.enabled) && rows.length > 0 && !allOn
          : anyOn && !allOn;
        return (
          <div key={kind} className="space-y-1.5">
            <label className="flex cursor-pointer items-center gap-2">
              <SectionMasterCheckbox
                checked={masterChecked}
                indeterminate={masterMixed}
                testid={`source-section-toggle-${kind}`}
                onChange={(enabled) =>
                  isApify
                    ? toggle.mutate({ id: "apify", enabled })
                    : toggle.mutate({ ids: rows.map((s) => s.id), enabled })
                }
              />
              <span className="text-[12px] font-semibold text-ink-2">
                {t(heading)}
              </span>
            </label>
            <div className="text-[11.5px] text-ink-4">{t(blurb)}</div>
            {isApify && rows.length === 0 ? (
              <div className="text-[11.5px] text-ink-4">
                {t("settingsPage.sources.apifyEmpty")}
              </div>
            ) : null}
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 pt-1 pl-6">
              {rows.map((s) => (
                <label
                  key={s.id}
                  className="flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2"
                  data-testid={`source-toggle-${s.id}`}
                >
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    onChange={(e) => toggle.mutate({ id: s.id, enabled: e.target.checked })}
                  />
                  <span className={s.enabled ? "" : "text-ink-4"}>{s.label}</span>
                  {s.entries > 0 ? (
                    <span className="text-[11px] text-ink-4">
                      {t("settingsPage.sources.boardCount", { count: s.entries })}
                    </span>
                  ) : null}
                </label>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
});

// Bring-your-own-key scraper credentials — split out of the sources list into
// its own Section card (maintainer 2026-07-23: "Provide your API Keys").
export const DiscoveryKeysSection = memo(function DiscoveryKeysSection() {
  const { t } = useTranslation();
  return (
    <div className="space-y-3" data-testid="discovery-keys">
      <p className="text-[11.5px] text-ink-4">
        {t("settingsPage.sources.keys.intro")}
      </p>
      <CredentialRow
        id="apify"
        label={t("settingsPage.sources.keys.apifyLabel")}
        hint={t("settingsPage.sources.keys.apifyHint")}
      />
      <CredentialRow
        id="brave"
        label={t("settingsPage.sources.keys.braveLabel")}
        hint={t("settingsPage.sources.keys.braveHint")}
      />
    </div>
  );
});
