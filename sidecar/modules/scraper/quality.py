"""Trust/quality checks at ingest (Track M3 spec).

Mirrors career-ops's `_trust-validator.mjs` stance (score + tier, scanner
side) with JustHireMe's red-flag list idea — but rank-don't-gate (vision
ethos): a low score *annotates* the row, it never drops it. The only rows
the pipeline discards are structurally broken ones (no title / no usable
URL), and those are counted per source, not silently vanished.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .types import NormalizedJob

# Scam / exploitative-posting terms (JustHireMe quality-gate red-flag class,
# re-implemented under MIT — Non-negotiable #1: design lesson, not code).
RED_FLAG_TERMS = (
    "unpaid",
    "equity only",
    "equity-only",
    "commission only",
    "commission-only",
    "for exposure",
    "no salary",
    "pay to apply",
    "registration fee",
    "training fee",
)

STALE_AFTER_DAYS = 60


def _parse_posted_at(posted_at: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(posted_at)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def assess(job: NormalizedJob, now: datetime | None = None) -> None:
    """Annotate `job.trust_score` (0–100) + `job.trust_flags` in place."""
    score = 100
    flags: list[str] = []

    if job.canonical_url.startswith("http://"):
        score -= 20
        flags.append("insecure-url")

    haystack = f"{job.title}\n{job.description}".lower()
    for term in RED_FLAG_TERMS:
        if term in haystack:
            score -= 40
            flags.append(f"red-flag-term:{term}")
            break  # one hit is signal enough; don't stack to zero on synonyms

    letters = [c for c in job.title if c.isalpha()]
    if len(letters) > 6 and all(c.isupper() for c in letters):
        score -= 10
        flags.append("shouty-title")

    if len(job.title) > 120:
        score -= 5
        flags.append("overlong-title")

    if not job.location.strip():
        score -= 5
        flags.append("no-location")

    if job.posted_at:
        dt = _parse_posted_at(job.posted_at)
        if dt is None:
            flags.append("unparseable-posted-date")
        else:
            age_days = ((now or datetime.now(UTC)) - dt).days
            if age_days > STALE_AFTER_DAYS:
                score -= 10
                flags.append("stale-posting")
    else:
        flags.append("no-posted-date")

    job.trust_score = max(score, 0)
    job.trust_flags = flags


def is_structurally_broken(job: NormalizedJob) -> bool:
    """True for rows the pipeline cannot act on at all (dropped + counted)."""
    return not job.title.strip() or not job.canonical_url
