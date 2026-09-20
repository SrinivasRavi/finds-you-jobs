export function formatEngineError(errorText: string | null | undefined, t?: any): string | null | undefined {
  if (!errorText) return errorText;
  if (errorText.includes("oauth_not_allowed") || errorText.includes("organization has disabled Claude subscription access")) {
    return t ? t("popups.applier.claudeSubscriptionError") : "Claude Code CLI authentication failed. Please check if your Claude subscription is active and connected.";
  }
  return errorText;
}
