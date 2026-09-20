// Stage-dependent message templates for the contact detail modal's composer
// (maintainer spec, 2026-08-14). Each mapped kanban stage offers a FEW plain
// starting points — the user picks one from a dropdown, the box prefills, and
// the text stays fully editable before the per-action confirm.
//
// The mapping: Accepted → a reminder (they accepted but never responded);
// Engagement → a referral ask personalized with the contact's own employer;
// Ghosted → a reminder after a long silence. Engagement degrades to "your
// company" when the employer field is blank; the greeting degrades to a plain
// "Hi," when the name is blank.
//
// FUTURE (explicit maintainer direction, deliberately NOT built yet): a later
// iteration auto-picks or randomizes the option instead of this manual
// dropdown. Until the maintainer asks, selection stays manual.

import type { ConnectionStatus } from "../api/types";

/** The slice of i18next's `t` this module needs — structural, so tests can
 *  hand in a fixed-locale translator without the full TFunction generics. */
export type Translator = (key: string, opts?: Record<string, unknown>) => string;

export interface StageTemplateOption {
  /** Stable id (`<stage>-<variant>`) for the dropdown value. */
  id: string;
  /** Localized dropdown label. */
  label: string;
  /** The full prefill (greeting + body), ready for the editable box. */
  body: string;
}

export function firstNameOf(name: string): string {
  return name.trim().split(/\s+/)[0] ?? "";
}

/** The per-stage variant lists. `company: true` marks a body with a
 *  `…Body` / `…BodyNoCompany` key pair, personalized when the employer is known. */
const STAGE_VARIANTS: Partial<
  Record<ConnectionStatus, { variant: string; company?: boolean }[]>
> = {
  accepted: [{ variant: "gentle" }, { variant: "direct" }, { variant: "context" }],
  // They wrote last, so our reply IS the moment to ask (S-N5).
  pending_our_response: [
    { variant: "direct", company: true },
    { variant: "soft", company: true },
    { variant: "advice", company: true },
  ],
  // Ours was last, so the fitting move is a nudge, warmer than the ghosted set
  // because this person has already written back at least once.
  pending_their_response: [
    { variant: "gentle" },
    { variant: "check" },
    { variant: "offer" },
  ],
  ghosted: [{ variant: "gentle" }, { variant: "direct" }, { variant: "reconnect" }],
};

/** The template options for a contact's stage — [] for stages with no mapping
 *  (the composer then starts blank, fully manual). */
export function stageTemplateOptions(
  stage: ConnectionStatus,
  contact: { name: string; current_company: string },
  t: Translator,
): StageTemplateOption[] {
  const variants = STAGE_VARIANTS[stage];
  if (!variants) return [];
  // The status is snake_case on the wire; the i18n namespace is camelCase.
  const ns = stage.replace(/_([a-z])/g, (_m, c: string) => c.toUpperCase());
  const firstName = firstNameOf(contact.name);
  const greeting = firstName
    ? t("networking.compose.greeting", { firstName })
    : t("networking.compose.greetingNoName");
  const company = contact.current_company.trim();
  return variants.map(({ variant, company: personalized }) => {
    const base = `networking.compose.templates.${ns}.${variant}`;
    const body =
      personalized && !company
        ? t(`${base}BodyNoCompany`)
        : t(`${base}Body`, personalized ? { company } : undefined);
    return { id: `${stage}-${variant}`, label: t(base), body: `${greeting}\n\n${body}` };
  });
}
