// Scan-preference vocabularies for Onboarding and the finder modal (D-F12); a
// second copy dropped "Any", so a no-window preference couldn't round-trip.
// Freshness label to days; 0 means "Any", no freshness window.

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
