// Shared helpers for the ReferralsModal component family (extracted 2026-07-25,
// F-M6 monolith split — pure moves, zero behavior change).

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
}
