"""The zero-LLM fit scorer: Settings > Scoring "Keyword scoring", and the grey
floor behind a failed AI score. Free, 1 ms a job, and honest about what word
overlap can and cannot tell you.

**Not a port.** JustHireMe's `ranking/scoring_engine.py` was read in full as a
structural reference before writing this — weighted criteria plus a hard-cap
floor is a sound shape, worth learning from. Its actual vocabulary is not:
it's calibrated for freelance-gig postings ("Gig Title", parsing a `$` figure
as a project *budget*, red flags like "lowest bidder"/"commission only"), not
W2 job postings, and porting it directly would import irrelevant signal into
this context (docs/internal/discovery.md, "JustHireMe scoring" entry,
2026-07-21). This rubric is written fresh and deliberately simple — a real v0
second opinion, not a claim of parity with the LLM scorer.

Reuses what the codebase already has instead of re-detecting it: red-flag
terms come straight from `scraper.quality.RED_FLAG_TERMS`, which already runs
live in the discovery pipeline, rather than a second hand-maintained list.

Weighted criteria, one modifier, one hard cap:
- Skill/keyword overlap (55%, or 60% when no level is readable): normalized
  word overlap between the resume and the JD text. No taxonomy, no embeddings.
- Years-of-experience match (30%, or 40%): the JD's stated "N+ years"
  requirement (if any) against the resume's own stated tenure (if any).
- Level fit (15%): the rung the title states against the best rung the resume
  states. Dropped from the weighting entirely when either side states none,
  which is 23 of 93 real titles, so an unstated level is never scored as a bad
  one.
- Location modifier (-15): a posting in a location the user did not ask for,
  from `modules/geo`. The same policy the LLM path applies, so the 2 stay
  comparable, and a location that cannot be read from the field costs nothing.
- Red-flag cap — a `quality.RED_FLAG_TERMS` hit clamps the score to 40
  regardless of the weighted total (a real fabrication/scam-risk floor, not a
  soft nudge).

Tagged `scorer_impl="scorer-deterministic"` at the caller (`JobScore` is
already schema-ready for more than one scorer implementation per job — no
migration needed) so it is never confused with `scorer-llm` output.
"""

from __future__ import annotations

import re

from sidecar.modules.geo.resolve import Verdict, Wanted, match, resolve_location
from sidecar.modules.scraper.quality import RED_FLAG_TERMS

from .level import LEVEL_ORDER, Level, extract_level
from .types import Factor, ScoreResult, Usage

# The location penalty lives beside the arithmetic that applies it. This module
# used to restate the number (duplication audit D-M18).
from .weights import LOCATION_MISS_PENALTY

_STOPWORDS = frozenset(
    """
    a an the and or but if then else for of to in on at by with from as is are
    was were be been being have has had do does did will would could should
    may might must can this that these those it its your you our we they he
    she their his her not no yes into out up down over under about across per
    etc via using use used within also including include includes required
    requirements responsibilities preferred plus years experience role team
    """.split()
)

_WORD_RE = re.compile(r"[a-z][a-z0-9+#./-]{1,}")
_YEARS_RE = re.compile(r"(\d{1,2})\+?\s*(?:years|yrs)\b")

# Below this many characters a JD is too thin to rate, and the answer is 0.
#
# The skill criterion divides overlap by the JD's OWN term count, so a short JD
# is graded on a tiny denominator: one matching word moves a 250-term posting by
# 0.2 points and an 8-term title header by 7.5. The years criterion compounds it,
# returning a neutral-positive 70 whenever the JD names no bar — which a header
# never can — so 40% of such a score is a constant and the floor is 28 even with
# zero overlap. Net effect measured on the maintainer's install 2026-08-27: jobs
# with NO description averaged 53.0 against 44.9 for described ones, and 34% of
# what counted as a "match" was company or city text (`india` matched 211 times).
# The scorer was rewarding the absence of a job description and ranking those
# jobs above properly-described ones (S-C27, and S-A6 for why they arrive empty).
#
# 0 is a refusal to rate, carried in `reasons`, not a claim that the role is bad.
# It sorts an unassessable job below every assessed one, which is where it goes.
#
# 200 is the same bar `_shared/job_input.py` already uses to reject a fetched
# page as "likely a JS-only page", so the codebase keeps one number for "too
# little text to be a job description". Safely clear of real postings: on that
# same install the shortest genuine description is 464 chars and nothing at all
# falls between 0 and 464.
MIN_JD_CHARS = 200

# A red-flag hit clamps the keyword score here. The weight lens re-applies it.
RED_FLAG_CAP = 40


def _tokenize(text: str) -> set[str]:
    # Trailing punctuation strip matters: the word regex allows internal
    # '.'/'/'/'-' so compound tokens like "node.js" tokenize as one word, but
    # that same class also swallows a sentence-ending period ("Django." vs
    # "Django" would otherwise never match across resume and JD text).
    words = (w.rstrip(".,;:!?-/") for w in _WORD_RE.findall((text or "").lower()))
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _max_years(text: str) -> int:
    years = [int(m) for m in _YEARS_RE.findall((text or "").lower())]
    return max(years) if years else 0


def _skill_overlap(master_md: str, job_text: str) -> tuple[int, set[str], set[str]]:
    resume_terms = _tokenize(master_md)
    jd_terms = _tokenize(job_text)
    if not jd_terms:
        return 50, set(), jd_terms  # no JD signal either way — neutral, not zero
    overlap = jd_terms & resume_terms
    return round(100 * len(overlap) / len(jd_terms)), overlap, jd_terms


def _years_match(master_md: str, job_text: str) -> tuple[int, int, int]:
    required = _max_years(job_text)
    offered = _max_years(master_md)
    if required == 0:
        return 70, required, offered  # JD names no explicit bar — neutral-positive
    if offered >= required:
        return 100, required, offered
    if offered >= required - 2:
        return 60, required, offered
    return 25, required, offered


_LEVEL_RANK = {level: i for i, level in enumerate(LEVEL_ORDER)}


def _resume_level(master_md: str) -> Level | None:
    """The best rung the resume states anywhere, which is the candidate's own.

    Highest rather than most recent, because a resume lists roles in no reliable
    order and a rung already held is a rung held.
    """
    found = [lvl for line in (master_md or "").splitlines() if (lvl := extract_level(line))]
    return max(found, key=lambda lvl: _LEVEL_RANK[lvl]) if found else None


def _level_fit(title: str, master_md: str) -> tuple[int, str] | None:
    """0 to 100 for how close the 2 rungs are, or None when either is unstated."""
    posting = extract_level(title)
    candidate = _resume_level(master_md)
    if posting is None or candidate is None:
        return None
    distance = abs(_LEVEL_RANK[posting] - _LEVEL_RANK[candidate])
    score = {0: 100, 1: 75, 2: 50}.get(distance, 25)
    return score, f"posting reads {posting}, resume reads {candidate}"


def score_deterministic(
    master_md: str,
    job_text: str,
    *,
    title: str = "",
    location: str = "",
    wanted: Wanted | None = None,
) -> ScoreResult:
    """Score `master_md` against raw `job_text` with zero LLM calls.

    `title`, `location` and `wanted` are optional so every existing caller keeps
    working: without them the level and location axes are simply not assessed,
    which is the same honest absence as a posting that states neither.

    A JD under `MIN_JD_CHARS` scores 0 — see that constant for why rating one is
    worse than refusing to."""
    if len((job_text or "").strip()) < MIN_JD_CHARS:
        return ScoreResult(
            score=0,
            reasons=[
                "No usable job description: "
                f"{len((job_text or '').strip())} chars, under the {MIN_JD_CHARS}-char "
                "minimum. Scoring a title and company name measures neither."
            ],
            breakdown_md=(
                "- No score: the posting carries no job description, so fit can't be "
                "assessed. This is a missing-data marker, not a judgement on the role."
            ),
            usage=Usage(),
        )

    skill_score, overlap, jd_terms = _skill_overlap(master_md, job_text)
    years_score, required_years, offered_years = _years_match(master_md, job_text)
    level = _level_fit(title, master_md)

    if level is None:
        skill_weight, years_weight, level_weight = 0.6, 0.4, 0.0
    else:
        skill_weight, years_weight, level_weight = 0.55, 0.3, 0.15
    weighted = round(
        skill_weight * skill_score
        + years_weight * years_score
        + level_weight * (level[0] if level else 0)
    )

    factors = [
        Factor(
            "core_stack",
            skill_score / 100,
            evidence=f"{len(overlap)} of {len(jd_terms)} posting terms appear in the resume",
        ),
        Factor(
            "years",
            years_score / 100,
            evidence=(
                f"{offered_years or 'unstated'} on the resume against "
                f"{required_years or 'unstated'} required"
            ),
        ),
    ]
    reasons = [
        f"Skill-keyword overlap: {len(overlap)}/{len(jd_terms)} JD terms found in "
        f"resume ({skill_score}/100, weight {skill_weight:.0%}).",
        f"Experience: {offered_years or 'unstated'} yrs on resume vs "
        f"{required_years or 'unstated'} yrs required ({years_score}/100, weight "
        f"{years_weight:.0%}).",
    ]
    breakdown_lines = [
        f"- Skill overlap: {skill_score}/100 (weight {skill_weight:.0%})",
        f"- Experience match: {years_score}/100 (weight {years_weight:.0%})",
    ]
    if level is not None:
        level_score, level_why = level
        factors.append(Factor("level", level_score / 100, evidence=level_why))
        reasons.append(f"Seniority: {level_why} ({level_score}/100, weight 15%).")
        breakdown_lines.append(f"- Seniority fit: {level_score}/100 (weight 15%), {level_why}")

    penalty = 0
    if wanted is not None and location.strip():
        verdict, why = match(resolve_location(location), wanted)
        if verdict is not Verdict.UNVERIFIABLE:
            factors.append(
                Factor(
                    "location_preference",
                    1.0 if verdict is Verdict.MATCH else 0.0,
                    evidence=why,
                )
            )
        if verdict is Verdict.MISS:
            penalty = LOCATION_MISS_PENALTY
        reasons.append(f"Location: {why}")
        breakdown_lines.append(f"- Location: {why}")

    lowered = (job_text or "").lower()
    red_flag = next((term for term in RED_FLAG_TERMS if term in lowered), None)
    final = max(0, weighted - penalty)
    if penalty:
        breakdown_lines.append(
            f"- Location modifier: -{penalty} for a location you did not ask for"
        )
    if red_flag:
        final = min(final, RED_FLAG_CAP)
        reasons.append(
            f"Cap applied: red-flag term {red_flag!r} detected — capped at {RED_FLAG_CAP}."
        )
        breakdown_lines.append(
            f"- Cap: red-flag term {red_flag!r} detected (score capped at {RED_FLAG_CAP})"
        )

    return ScoreResult(
        score=final,
        reasons=reasons,
        breakdown_md="\n".join(breakdown_lines),
        factors=factors,
        usage=Usage(),  # zero-LLM — no tokens/cost, matches Usage's "free beyond bandwidth"
    )
