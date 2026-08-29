"""Deterministic (zero-LLM) fit scorer — a second opinion alongside the LLM
Scorer, built for the deterministic-scoring evaluation (experiment branch
`experiment/deterministic-scoring`, not shipped on main).

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

Two weighted criteria, one hard cap:
- Skill/keyword overlap (60%) — normalized word overlap between the resume
  and the JD text. Simple by design: no taxonomy, no embeddings.
- Years-of-experience match (40%) — the JD's stated "N+ years" requirement
  (if any) against the resume's own stated tenure (if any).
- Red-flag cap — a `quality.RED_FLAG_TERMS` hit clamps the score to 40
  regardless of the weighted total (a real fabrication/scam-risk floor, not a
  soft nudge).

Tagged `scorer_impl="scorer-deterministic"` at the caller (`JobScore` is
already schema-ready for more than one scorer implementation per job — no
migration needed) so it is never confused with `scorer-llm` output.
"""

from __future__ import annotations

import re

from sidecar.modules.scraper.quality import RED_FLAG_TERMS

from .types import ScoreResult, Usage

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


def score_deterministic(master_md: str, job_text: str) -> ScoreResult:
    """Score `master_md` against raw `job_text` with zero LLM calls.

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
    weighted = round(0.6 * skill_score + 0.4 * years_score)

    lowered = (job_text or "").lower()
    red_flag = next((term for term in RED_FLAG_TERMS if term in lowered), None)
    final = min(weighted, 40) if red_flag else weighted

    reasons = [
        f"Skill-keyword overlap: {len(overlap)}/{len(jd_terms)} JD terms found in "
        f"resume ({skill_score}/100, weight 60%).",
        f"Experience: {offered_years or 'unstated'} yrs on resume vs "
        f"{required_years or 'unstated'} yrs required ({years_score}/100, weight 40%).",
    ]
    breakdown_lines = [
        f"- Skill overlap: {skill_score}/100 (weight 60%)",
        f"- Experience match: {years_score}/100 (weight 40%)",
    ]
    if red_flag:
        reasons.append(f"Cap applied: red-flag term {red_flag!r} detected — capped at 40.")
        breakdown_lines.append(f"- Cap: red-flag term {red_flag!r} detected (score capped at 40)")

    return ScoreResult(
        score=final,
        reasons=reasons,
        breakdown_md="\n".join(breakdown_lines),
        usage=Usage(),  # zero-LLM — no tokens/cost, matches Usage's "free beyond bandwidth"
    )
