// Applicant details (audit A5, maintainer 2026-10-13): what the Applier types into
// forms, read from the master resume and confirmed by the user once before the
// first apply. 1 form, shown in Settings > Applying and as the dialog Apply opens.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useCountries,
  useExtractApplicationProfile,
  useProfile,
  useSaveApplicantDetails,
} from "../../api/queries";
import type {
  ApplicationProfile,
  WorkAuthStatus,
  WorkAuthorization,
} from "../../api/types";
import { Modal } from "../../shell/Modal";

const STATUSES: WorkAuthStatus[] = ["citizen", "permanent_resident", "visa", "needs_sponsorship"];
const INPUT =
  "h-[30px] w-full rounded-md border border-border-2 bg-surface px-2 text-[12.5px] text-ink";

/** "label: url" per line, read back into the record's label-to-URL map. A bare URL
 *  is labelled by its host, the way the resume read labels them. */
function linksToText(links: ApplicationProfile["links"]): string {
  return Object.entries(links ?? {})
    .map(([label, url]) => `${label}: ${url}`)
    .join("\n");
}

function textToLinks(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const m = /^([a-z][\w -]*):\s*(\S+)$/i.exec(line);
    const url = m && !/^https?$/i.test(m[1]) ? m[2] : line;
    const label = m && !/^https?$/i.test(m[1])
      ? m[1].toLowerCase()
      : /linkedin\./i.test(url) ? "linkedin" : /github\./i.test(url) ? "github" : "portfolio";
    out[out[label] ? `${label} ${Object.keys(out).length + 1}` : label] = url;
  }
  return out;
}

export function ApplicantDetailsForm({
  onSaved,
  saveLabel,
  intro = true,
}: {
  onSaved?: () => void;
  saveLabel?: string;
  /** Off in the dialog, which says it in its own words. */
  intro?: boolean;
}) {
  const { t } = useTranslation();
  const { data: profile } = useProfile();
  const { data: countries } = useCountries();
  const save = useSaveApplicantDetails();
  const extract = useExtractApplicationProfile();
  const record = profile?.application_profile ?? null;
  const [draft, setDraft] = useState<ApplicationProfile>({});
  const [links, setLinks] = useState("");
  const [savedOnce, setSavedOnce] = useState(false);

  // Reseed whenever the stored record changes (a resume read landing, a save).
  useEffect(() => {
    setDraft({ ...(record ?? {}) });
    setLinks(linksToText(record?.links));
  }, [record]);

  const auths = draft.work_authorizations ?? [];
  const setAuth = (i: number, part: Partial<WorkAuthorization>) =>
    setDraft((d) => ({
      ...d,
      work_authorizations: (d.work_authorizations ?? []).map((a, j) =>
        j === i ? { ...a, ...part } : a,
      ),
    }));
  const field = (key: "first_name" | "last_name" | "email" | "phone" | "location") => (
    <label className="block">
      <span className="mb-1 block text-[11.5px] text-ink-3">
        {t(`settingsPage.applicant.${key === "first_name" ? "firstName" : key === "last_name" ? "lastName" : key}`)}
      </span>
      <input
        data-testid={`applicant-${key}`}
        className={INPUT}
        value={String(draft[key] ?? "")}
        onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
      />
    </label>
  );

  const submit = async () => {
    const work = auths.filter((a) => a.country.trim());
    await save.mutateAsync({ ...draft, links: textToLinks(links), work_authorizations: work });
    setSavedOnce(true);
    onSaved?.();
  };

  return (
    <div className="space-y-4" data-testid="applicant-details">
      {intro ? <p className="text-[12px] text-ink-3">{t("settingsPage.applicant.intro")}</p> : null}
      {!record ? (
        <button
          type="button"
          data-testid="applicant-fill"
          disabled={extract.isPending}
          onClick={() => extract.mutate()}
          className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12px] text-ink-2 hover:bg-surface-3 disabled:text-ink-4"
        >
          {extract.isPending || extract.isSuccess
            ? t("settingsPage.applicant.filling")
            : t("settingsPage.applicant.fill")}
        </button>
      ) : null}
      <div className="grid grid-cols-2 gap-3">
        {field("first_name")}
        {field("last_name")}
        {field("email")}
        {field("phone")}
      </div>
      <div>
        {field("location")}
        <p className="mt-1 text-[11px] text-ink-4">{t("settingsPage.applicant.locationHint")}</p>
      </div>
      <label className="block">
        <span className="mb-1 block text-[11.5px] text-ink-3">{t("settingsPage.applicant.links")}</span>
        <textarea
          data-testid="applicant-links"
          rows={3}
          className="w-full rounded-md border border-border-2 bg-surface px-2 py-1.5 text-[12.5px] text-ink"
          value={links}
          onChange={(e) => setLinks(e.target.value)}
        />
        <span className="text-[11px] text-ink-4">{t("settingsPage.applicant.linksHint")}</span>
      </label>
      <div>
        <div className="text-[12.5px] font-medium text-ink">{t("settingsPage.applicant.workAuth")}</div>
        <p className="mb-2 text-[11px] text-ink-4">{t("settingsPage.applicant.workAuthHint")}</p>
        {draft.work_authorization && auths.length === 0 ? (
          <p className="mb-2 text-[11.5px] text-ink-3">“{draft.work_authorization}”</p>
        ) : null}
        <div className="space-y-2">
          {auths.map((a, i) => (
            <div key={i} className="flex items-center gap-2" data-testid="applicant-auth-row">
              <select
                aria-label={t("settingsPage.applicant.country")}
                data-testid="applicant-auth-country"
                className={`${INPUT} !w-[32%] shrink-0`}
                value={a.country}
                onChange={(e) => setAuth(i, { country: e.target.value })}
              >
                <option value="">{t("settingsPage.applicant.country")}</option>
                {(countries ?? []).map((c) => (
                  <option key={c.code} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </select>
              <select
                aria-label={t("settingsPage.applicant.status")}
                data-testid="applicant-auth-status"
                className={`${INPUT} !w-[28%] shrink-0`}
                value={a.status}
                onChange={(e) => setAuth(i, { status: e.target.value as WorkAuthStatus })}
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t(`settingsPage.applicant.status_${s}`)}
                  </option>
                ))}
              </select>
              <input
                aria-label={t("settingsPage.applicant.note")}
                placeholder={t("settingsPage.applicant.note")}
                className={`${INPUT} min-w-0 flex-1`}
                value={a.note ?? ""}
                onChange={(e) => setAuth(i, { note: e.target.value })}
              />
              <button
                type="button"
                className="text-[11.5px] text-ink-3 hover:text-ink"
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    work_authorizations: (d.work_authorizations ?? []).filter((_, j) => j !== i),
                  }))
                }
              >
                {t("settingsPage.applicant.remove")}
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          data-testid="applicant-add-auth"
          className="mt-2 text-[12px] text-accent hover:underline"
          onClick={() =>
            setDraft((d) => ({
              ...d,
              work_authorizations: [
                ...(d.work_authorizations ?? []),
                { country: "", status: "citizen" },
              ],
            }))
          }
        >
          {t("settingsPage.applicant.addCountry")}
        </button>
      </div>
      <div className="flex items-center justify-end gap-3">
        {savedOnce && !save.isPending ? (
          <span className="text-[11.5px] text-ink-3">{t("settingsPage.applicant.saved")}</span>
        ) : null}
        <button
          type="button"
          data-testid="applicant-save"
          disabled={save.isPending}
          onClick={() => void submit().catch(() => undefined)}
          className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {saveLabel ?? t("settingsPage.applicant.save")}
        </button>
      </div>
    </div>
  );
}

/** The dialog Apply opens until the details are confirmed once. */
export function ApplicantDetailsDialog({
  onSaved,
  onCancel,
}: {
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal title={t("settingsPage.applicant.dialogTitle")} onClose={onCancel} size="md">
      <div className="px-5 py-4" data-testid="applicant-dialog">
        <p className="mb-3 text-[12.5px] text-ink-2">{t("settingsPage.applicant.dialogBody")}</p>
        <ApplicantDetailsForm
          onSaved={onSaved}
          saveLabel={t("settingsPage.applicant.saveAndApply")}
          intro={false}
        />
      </div>
    </Modal>
  );
}
