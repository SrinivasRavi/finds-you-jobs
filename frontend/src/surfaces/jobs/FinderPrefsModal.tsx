import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import i18n from "../../i18n";
import { invalidateFeed, qk } from "../../api/queries";
import { useProfile, useSchedules, useTriggerScan } from "../../api/queries";
import { type PlaceChip, type Settings } from "../../api/types";
import { ChipInput } from "../../shell/ChipInput";
import { PlaceInput } from "../../shell/PlaceInput";
import { Modal } from "../../shell/Modal";
import { CADENCE_OPTIONS, FRESHNESS_DAYS, FRESHNESS_LABEL, FRESHNESS_OPTIONS } from "../scanPrefs";
import { firstHeading } from "../jobFormat";

// ─── Job finder preferences (US-JB-01 topbar / US-SET-01; doubles as settings-prefs) ──

function RadioRow({
  options,
  value,
  onChange,
  testid,
  display,
}: {
  options: readonly string[];
  value: string;
  onChange: (v: string) => void;
  testid: string;
  /** Maps a persisted option VALUE to its displayed (translated) label. */
  display?: (v: string) => string;
}) {
  return (
    <div
      data-testid={testid}
      role="radiogroup"
      className="inline-flex rounded-7 border border-border-2 bg-surface p-0.5"
    >
      {options.map((o) => (
        <button
          key={o}
          type="button"
          role="radio"
          aria-checked={value === o}
          onClick={() => onChange(o)}
          className={
            "h-[28px] rounded-[5px] px-3 text-[12.5px] font-medium " +
            (value === o ? "bg-accent text-white" : "text-ink-2 hover:bg-surface-3")
          }
        >
          {display ? display(o) : o}
        </button>
      ))}
    </div>
  );
}


// "in 5h" / "in 2d" for the next-automatic-scan line; "overdue — any minute"
// when the tick hasn't caught up yet.
function nextScanLabel(iso: string): string {
  const mins = Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
  if (mins <= 0) return i18n.t("jobBoard.prefs.nextScanOverdue");
  if (mins < 60) return i18n.t("jobBoard.prefs.inMinutes", { n: mins });
  if (mins < 48 * 60) return i18n.t("jobBoard.prefs.inHours", { n: Math.round(mins / 60) });
  return i18n.t("jobBoard.prefs.inDays", { n: Math.round(mins / (24 * 60)) });
}

export function FinderPrefsModal({
  prefs,
  onClose,
}: {
  prefs: Settings["job_prefs"];
  onClose: () => void;
}) {
  // Seeded from the STORED preferences and saved back through the same
  // /api/settings path onboarding uses — which also threads the cadence into
  // the scan schedule server-side. (2026-07-12 audit P0-2: this modal was a
  // hardcoded mock stub whose "Save & rescan" rescanned with stale prefs.)
  //
  // `prefs` is a required prop, not a query read, because these are `useState`
  // seeds: they run once and nothing re-seeds them. Reading an unresolved
  // query here left roles and locations empty for the modal's whole life, and
  // the first edit then autosaved those empties over the user's real excludes
  // (S-C72).
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [roles, setRoles] = useState<string[]>(prefs.role_aliases);
  const [locations, setLocations] = useState<PlaceChip[]>(prefs.locations);
  const [freshness, setFreshness] = useState(
    FRESHNESS_LABEL[prefs.freshness_days] ?? "7d",
  );
  const [cadence, setCadence] = useState(prefs.scrape_cadence);
  const [excludedCompanies, setExcludedCompanies] = useState<string[]>(prefs.excluded_companies);
  const [excludedKeywords, setExcludedKeywords] = useState<string[]>(prefs.excluded_keywords);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);
  const { data: profile } = useProfile();
  const triggerScan = useTriggerScan();
  const masterName = firstHeading(profile?.master_md);
  const { data: schedules } = useSchedules();
  const scanSchedule = schedules?.find((s) => s.kind === "scan");

  // Autosave (maintainer 2026-07-22 #2): edits persist on their own, matching
  // the rest of the app's instant-persist controls (Settings toggles, the
  // Tracked companies roster right above). "Rescan now" only triggers the
  // scan — flushing any still-debouncing edit first so it never scans stale
  // values. The description carries the "auto saved" promise; the footer-left
  // "Changes saved!" is the per-edit confirmation.
  const snapshot = JSON.stringify({
    roles,
    locations,
    freshness,
    cadence,
    excludedCompanies,
    excludedKeywords,
  });
  const lastSaved = useRef(snapshot);

  async function persist(snap: string): Promise<void> {
    setSaveError("");
    // networking_enabled deliberately omitted — this modal never touches it.
    await api.savePreferences({
      role_aliases: roles,
      locations,
      freshness_days: FRESHNESS_DAYS[freshness] ?? 7,
      scrape_cadence: cadence,
      excluded_companies: excludedCompanies,
      excluded_keywords: excludedKeywords,
    });
    lastSaved.current = snap;
    setSavedFlash(true);
    void qc.invalidateQueries({ queryKey: qk.settings });
    void qc.invalidateQueries({ queryKey: qk.schedules }); // next-scan line
    invalidateFeed(qc); // excludes hide board rows without waiting for a scan
  }

  const dirty = snapshot !== lastSaved.current;
  const valid = roles.length > 0 && locations.length > 0;
  useEffect(() => {
    if (!dirty || !valid) return;
    const t = setTimeout(() => {
      persist(snapshot).catch((e: unknown) =>
        setSaveError(e instanceof Error ? e.message : String(e)),
      );
    }, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- snapshot encodes every field
  }, [snapshot]);

  async function rescanNow() {
    setSaving(true);
    try {
      if (dirty && valid) await persist(snapshot);
      triggerScan.mutate(); // runs against the values just saved
      onClose();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  return (
    <Modal title={t("jobBoard.prefs.title")} onClose={onClose} size="md">
      <form
        className="flex flex-col gap-5 px-5 py-5"
        onSubmit={(e) => {
          e.preventDefault();
          void rescanNow();
        }}
      >
        <p className="-mt-2 text-[12px] text-ink-3">
          {t("jobBoard.prefs.intro")}
        </p>
        <ChipInput
          label={t("jobBoard.prefs.roles.label")}
          hint={t("jobBoard.prefs.roles.hint")}
          items={roles}
          onAdd={(v) => setRoles((r) => [...r, v])}
          onRemove={(v) => setRoles((r) => r.filter((x) => x !== v))}
          placeholder={t("jobBoard.prefs.roles.placeholder")}
          testid="fp-roles"
        />
        <PlaceInput
          label={t("jobBoard.prefs.locations.label")}
          hint={t("jobBoard.prefs.locations.hint")}
          items={locations}
          onAdd={(v) => setLocations((r) => [...r, v])}
          onRemove={(v) => setLocations((r) => r.filter((x) => x !== v))}
          placeholder={t("jobBoard.prefs.locations.placeholder")}
          testid="fp-locations"
        />
        <ChipInput
          label={t("jobBoard.prefs.excludedCompanies.label")}
          hint={t("jobBoard.prefs.excludedCompanies.hint")}
          items={excludedCompanies}
          onAdd={(v) => setExcludedCompanies((r) => [...r, v])}
          onRemove={(v) => setExcludedCompanies((r) => r.filter((x) => x !== v))}
          placeholder={t("jobBoard.prefs.excludedCompanies.placeholder")}
          testid="fp-exclude-companies"
        />
        <ChipInput
          label={t("jobBoard.prefs.excludedKeywords.label")}
          hint={t("jobBoard.prefs.excludedKeywords.hint")}
          items={excludedKeywords}
          onAdd={(v) => setExcludedKeywords((r) => [...r, v])}
          onRemove={(v) => setExcludedKeywords((r) => r.filter((x) => x !== v))}
          placeholder={t("jobBoard.prefs.excludedKeywords.placeholder")}
          testid="fp-exclude-keywords"
        />
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">{t("jobBoard.prefs.freshness.heading")}</h3>
            <p className="text-[11.5px] text-ink-3">
              {t("jobBoard.prefs.freshness.hint")}
            </p>
          </header>
          <RadioRow
            options={FRESHNESS_OPTIONS}
            value={freshness}
            onChange={setFreshness}
            testid="fp-freshness"
            display={(v) => t(`jobBoard.prefs.freshness.values.${v}`)}
          />
        </section>
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">{t("jobBoard.prefs.cadence.heading")}</h3>
            <p className="text-[11.5px] text-ink-3">
              {t("jobBoard.prefs.cadence.hint")}
            </p>
          </header>
          <RadioRow
            options={CADENCE_OPTIONS}
            value={cadence}
            onChange={setCadence}
            testid="fp-cadence"
            display={(v) => t(`jobBoard.prefs.cadence.values.${v}`)}
          />
          {/* Proof the cadence is real (2026-07-22): the scan schedule's actual
              next firing time. Saving here also rescans now, which pushes this
              out one full interval — by design, to avoid a double scan. */}
          {scanSchedule ? (
            <p className="text-[11.5px] text-ink-3" data-testid="fp-next-scan">
              {scanSchedule.enabled
                ? t("jobBoard.prefs.nextScan", { when: nextScanLabel(scanSchedule.next_due_at) })
                : t("jobBoard.prefs.autoScanOff")}
            </p>
          ) : null}
        </section>
        <section className="space-y-2">
          <header>
            <h3 className="text-[13px] font-semibold text-ink">{t("jobBoard.prefs.master.heading")}</h3>
            <p className="text-[11.5px] text-ink-3">{t("jobBoard.prefs.master.hint")}</p>
          </header>
          <div className="flex items-center justify-between gap-3 rounded-7 border border-border-2 bg-surface-2 px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-[12.5px] font-medium text-ink">
                {masterName
                  ? t("jobBoard.prefs.master.named", { name: masterName })
                  : t("jobBoard.prefs.master.none")}
              </div>
              <div className="text-[11px] text-ink-3">
                {masterName ? t("jobBoard.prefs.master.ready") : t("jobBoard.prefs.master.addOne")}
              </div>
            </div>
            {/* View/Replace opens the master-resume popup — returns with the
                resume-popup commit. */}
          </div>
        </section>
        <div className="-mx-5 -mb-5 mt-2 flex items-center justify-end gap-2 border-t border-border bg-surface-2 px-5 py-3">
          {/* Footer-left: the autosave confirmation (maintainer's ss1 spot);
              an error takes its place — never both. */}
          {saveError ? (
            <span className="mr-auto text-[11.5px] text-bad" data-testid="finder-prefs-error">
              {saveError}
            </span>
          ) : savedFlash && !dirty ? (
            <span className="mr-auto text-[11.5px] text-good" data-testid="finder-prefs-saved">
              {t("jobBoard.prefs.saved")}
            </span>
          ) : dirty && valid ? (
            <span className="mr-auto text-[11.5px] text-ink-3">{t("jobBoard.prefs.saving")}</span>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2 hover:text-ink"
          >
            {t("jobBoard.prefs.close")}
          </button>
          <button
            type="submit"
            disabled={saving || !valid}
            data-testid="finder-prefs-save"
            title={t("jobBoard.prefs.rescanTitle")}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? t("jobBoard.prefs.starting") : t("jobBoard.prefs.rescanNow")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ─── Trash (US-JB-11) ─────────────────────────────────────────────────────────
