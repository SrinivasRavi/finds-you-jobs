// Company-confirm step (FR-NW-02) — pick the right entity before we search its
// current employees. Single-select, distinct from the people multi-select in
// review. (Extracted from ReferralsModal.tsx 2026-07-25, F-M6 monolith split —
// pure move, zero behavior change. `pasteUrl` stays at the root: it must
// survive the confirm → searching → confirm bounce after a bad pasted URL,
// which unmounts this step.) Not memoized: it renders only during the
// `confirm` phase, and its confirm callback closes over root state — memo
// would buy nothing real.

import { Trans, useTranslation } from "react-i18next";

import type { CompanyCandidate } from "../../api/types";
import { Avatar } from "../../shell/Avatar";

export function CompanyConfirmStep({
  company,
  companyCandidates,
  pickedCompany,
  urlFailed,
  discoverPending,
  pasteUrl,
  onPasteUrl,
  onPick,
  onConfirmPicked,
  onConfirmUrl,
  onBack,
  onClose,
}: {
  company: string;
  companyCandidates: CompanyCandidate[];
  pickedCompany: string | null;
  urlFailed: boolean;
  discoverPending: boolean;
  pasteUrl: string;
  onPasteUrl: (v: string) => void;
  onPick: (key: string) => void;
  onConfirmPicked: () => void;
  onConfirmUrl: (url: string) => void;
  onBack: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  function confirmPastedUrl() {
    const url = pasteUrl.trim();
    if (!url) return;
    onConfirmUrl(url);
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden" data-testid="company-confirm">
      <div className="border-b border-border bg-surface-2 px-5 py-3 text-[12.5px] text-ink-2">
        {companyCandidates.length > 0 ? (
          <Trans
            i18nKey="popups.referrals.confirmIntroPick"
            values={{ company }}
            components={{ strong: <strong />, em: <em /> }}
          />
        ) : (
          <Trans
            i18nKey="popups.referrals.confirmIntroNoMatch"
            values={{ company }}
            components={{ strong: <strong /> }}
          />
        )}
      </div>
      {urlFailed && (
        <div
          className="border-b border-border bg-bad-wash px-5 py-2 text-[12px] font-medium text-bad"
          data-testid="company-url-failed"
        >
          <Trans
            i18nKey="popups.referrals.urlFailed"
            components={{ code: <code className="mx-1" /> }}
          />
        </div>
      )}
      <div className="flex-1 overflow-y-auto">
        {companyCandidates.map((c) => (
          <label
            key={c.urn}
            data-testid="company-candidate"
            className="flex cursor-pointer items-center gap-3 border-b border-border px-5 py-3 hover:bg-surface-2"
          >
            <input
              type="radio"
              name="company-pick"
              data-testid="company-candidate-radio"
              checked={pickedCompany === (c.urn || `v:${c.vanity}`)}
              onChange={() => onPick(c.urn || `v:${c.vanity}`)}
              className="h-4 w-4 cursor-pointer"
            />
            {c.logo_url ? (
              <img
                src={c.logo_url}
                alt=""
                className="h-8 w-8 shrink-0 rounded-md object-cover"
              />
            ) : (
              <Avatar name={c.name} shape="md" tone="raised" />
            )}
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2 text-[13px] font-medium text-ink">
                <span className="truncate">{c.name}</span>
                {c.domain_match && (
                  <span className="inline-flex h-[16px] items-center rounded-full border border-good bg-good-wash px-1.5 font-mono text-[9.5px] text-good">
                    {t("popups.referrals.bestMatch")}
                  </span>
                )}
              </span>
              {/* Subtitle: industry when known, else the company's slug —
                  something identifying, never a generic filler label. */}
              <span className="block truncate text-[11.5px] text-ink-3">
                {c.industry || (c.vanity ? `linkedin.com/company/${c.vanity}` : "")}
              </span>
            </span>
            {/* Verify link — open the company's LinkedIn page in a new tab. */}
            {c.vanity ? (
              <a
                href={`https://www.linkedin.com/company/${c.vanity}/`}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="company-candidate-link"
                onClick={(e) => e.stopPropagation()}
                className="shrink-0 rounded-md border border-border-2 bg-surface px-2 py-1 text-[11px] font-medium text-ink-2 hover:bg-surface-3"
              >
                {t("popups.referrals.linkedIn")}
              </a>
            ) : null}
          </label>
        ))}
      </div>
      {/* Paste the company's LinkedIn URL — the authoritative override */}
      <div className="flex items-center gap-2 border-t border-border bg-surface-2 px-5 py-2.5">
        <input
          type="url"
          data-testid="company-url-input"
          value={pasteUrl}
          onChange={(e) => onPasteUrl(e.target.value)}
          placeholder={t("popups.referrals.pasteUrlPlaceholder")}
          className="h-[30px] flex-1 rounded-md border border-border bg-surface px-2.5 text-[12px] text-ink focus:border-accent focus:outline-none"
        />
        <button
          data-testid="company-url-use-btn"
          disabled={!pasteUrl.trim() || discoverPending}
          onClick={confirmPastedUrl}
          className="h-[30px] rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("popups.referrals.useThisUrl")}
        </button>
      </div>
      <div className="flex items-center gap-2 border-t border-border bg-surface-2 px-5 py-3">
        <button
          data-testid="company-confirm-back"
          className="h-[30px] rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
          onClick={onBack}
        >
          {t("popups.referrals.back")}
        </button>
        <span className="text-[11px] text-ink-4">
          {t("popups.referrals.backHint")}
        </span>
        <span className="flex-1" />
        <button
          className="h-[30px] rounded-md px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
          onClick={onClose}
        >
          {t("popups.referrals.cancel")}
        </button>
        <button
          data-testid="company-confirm-btn"
          disabled={!pickedCompany || discoverPending}
          onClick={onConfirmPicked}
          className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("popups.referrals.findEmployees")}
        </button>
      </div>
    </div>
  );
}
