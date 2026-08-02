// The one initials tile (duplication audit D-F7). Ten hand-typed copies of the
// same grid + place-items-center markup had settled into six chrome variants —
// one of them carrying a comment admitting it "matches the Applications card
// avatar". What genuinely differs between the call sites (size, corner radius,
// wash) is a prop; everything else is shared, so the initials rule and the
// layout live in exactly one place.

/** Up to two leading initials, uppercased. Empty tokens are dropped first, so a
 *  stray leading or doubled space never eats one of the two slots. */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
}

/** Box size; the glyph size rides along with it. */
export type AvatarSize = 8 | 10 | 14;
export type AvatarShape = "sm" | "md" | "full";
export type AvatarTone = "card" | "raised" | "brand";

const SIZE_CLS: Record<AvatarSize, string> = {
  8: "h-8 w-8 text-[11px]",
  10: "h-10 w-10 text-[13px]",
  14: "h-14 w-14 text-[18px]",
};

const SHAPE_CLS: Record<AvatarShape, string> = {
  sm: "rounded",
  md: "rounded-md",
  full: "rounded-full",
};

const TONE_CLS: Record<AvatarTone, string> = {
  // Applications / Networking cards and the recovery rosters.
  card: "bg-surface-2 text-ink-2",
  // Referral rows, the company picker, the tracker job-detail block — raised
  // wash, monospaced.
  raised: "bg-surface-3 font-mono text-ink-2",
  // Job Board logo tile.
  brand: "bg-gradient-to-br from-accent-wash to-purple-wash text-accent-ink",
};

export function Avatar({
  name,
  size = 8,
  shape = "sm",
  tone = "card",
  decorative = false,
}: {
  name: string;
  size?: AvatarSize;
  shape?: AvatarShape;
  tone?: AvatarTone;
  /** Hide from assistive tech — the Job Board tiles repeat the company name
   *  that already sits beside them. */
  decorative?: boolean;
}) {
  return (
    <span
      aria-hidden={decorative || undefined}
      className={`grid shrink-0 place-items-center font-semibold ${SIZE_CLS[size]} ${SHAPE_CLS[shape]} ${TONE_CLS[tone]}`}
    >
      {initials(name)}
    </span>
  );
}
