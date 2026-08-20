// Scan-preference vocabularies shared by the Onboarding wizard and the Job
// finder preferences modal (duplication audit D-F12). Two copies of the same
// freshness/cadence tables had drifted: onboarding's freshness list omitted
// "Any", so a stored no-freshness-window preference could not round-trip
// through the wizard.
//
// Freshness label ⇄ days; 0 = "Any" = no freshness window (ScanPrefs semantics).

export const FRESHNESS_OPTIONS = ["24h", "7d", "30d", "Any"] as const;

export const FRESHNESS_DAYS: Record<string, number> = { "24h": 1, "7d": 7, "30d": 30, Any: 0 };

export const FRESHNESS_LABEL: Record<number, string> = { 1: "24h", 7: "7d", 30: "30d", 0: "Any" };

export const CADENCE_OPTIONS = [
  "Every 6h",
  "Every 12h",
  "Every 24h",
  "Every 48h",
  "Every 72h",
] as const;
