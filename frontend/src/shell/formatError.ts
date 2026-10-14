// 1 engine error, turned into something a user can act on. The sidecar's
// verbatim text is the default (NFR-SIDE-04); the exception is an error whose
// text is accurate but useless, where a sentence the user can follow is better.

import type { TFunction } from "i18next";

import i18n from "../i18n";

/** Patterns that mean "your Claude subscription can't be used from here". */
const CLAUDE_SUBSCRIPTION_MARKERS = [
  "oauth_not_allowed",
  "organization has disabled Claude subscription access",
];

/** Returns `errorText` unchanged unless we have better words for it. Passing
 *  `t` keeps the caller's active translation; without it the app-wide i18n
 *  instance is used, so the copy is never hardcoded English either way. */
export function formatEngineError(
  errorText: string | null | undefined,
  t?: TFunction,
): string | null | undefined {
  if (!errorText) return errorText;
  if (CLAUDE_SUBSCRIPTION_MARKERS.some((marker) => errorText.includes(marker))) {
    return (t ?? i18n.t)("popups.applier.claudeSubscriptionError");
  }
  return errorText;
}
