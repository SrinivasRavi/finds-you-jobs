"""Score a posting with TypeSafe's Jev: 1 request, 1 question per axis, no prose.

Jev is a decision model. It never generates text: it takes a `state` (here the
resume, the posting and the user's preferences) plus typed questions, and returns
a position on each question's own rubric plus a calibrated probability. So there
is no output contract to drift and nothing to parse.

**One question per axis, graded where the axis is graded** (maintainer, 2026-10-01,
`talk.md` D43). A noul is a probability that a proposition holds, and multiplying
one by a user's weight produces a number with no meaning, so only the 2 genuinely
binary axes stay nouls. The vendor allows any number of mixed questions in 1
request and bills output at $0, so 5 score questions plus 2 nouls costs what the
single-question version cost: $0.000223 a job, measured.

The total is our arithmetic over the factors (`weights.py`), which is the point of
asking per axis: the bars explain the number instead of accompanying it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .deterministic import MIN_JD_CHARS
from .types import Factor, ScoreError, ScoreResult, Usage
from .weights import JEV_WEIGHTS, weighted_total

MODEL = "typesafe/jev-1.13"

# Every graded axis with its own 5-band rubric, lowest band first. The bands are
# what the user reads as the evidence behind a bar, so they are written as
# findings about this candidate and this posting, not as grades.
BANDS: Mapping[str, tuple[str, ...]] = {
    "hard_requirements": (
        "None of the items the posting states as required are evidenced in the resume.",
        "One of several required items is evidenced; the rest have no support at all.",
        "About half the required items are evidenced; several have no support.",
        "Most required items are evidenced; one is missing or only partially supported.",
        "Every item the posting states as required is evidenced in the resume.",
    ),
    "core_stack": (
        "The posting's core technical stack is absent from the resume.",
        "Only surface familiarity with the core stack; no hands-on evidence.",
        "Hands-on with some of the core stack, missing its most central piece.",
        "Hands-on with most of the core stack, with one adjacent substitution.",
        "Hands-on production evidence for the whole core stack the role is built on.",
    ),
    "years": (
        "Far short of the experience the posting requires, or the resume shows none.",
        "Several years short of the stated bar.",
        "Within about 2 years of the stated bar.",
        "Meets the stated bar.",
        "Comfortably past the stated bar, or the posting states no bar and the resume is senior.",
    ),
    "level": (
        "The seniority gap is several rungs in either direction.",
        "2 rungs apart, in either direction.",
        "1 rung apart, in either direction.",
        "The same rung, read loosely.",
        "The resume's seniority is exactly the rung this posting is written for.",
    ),
    "nice_to_haves": (
        "None of the preferred items are evidenced.",
        "One preferred item is evidenced.",
        "About half the preferred items are evidenced.",
        "Most preferred items are evidenced.",
        "Every preferred item is evidenced, or the posting lists none.",
    ),
}

# The 2 axes that really are yes or no. `logistics_blocker` is asked as the
# blocker (a contradiction exists), so its factor value is inverted: a 0.9
# probability of a blocker is a 0.1 on the logistics axis.
NOULS: Mapping[str, str] = {
    "location_preference_met": (
        "The posting's own location is one of the locations the user asked for in their "
        "search preferences, or the posting is remote and open to someone in the "
        "candidate's country. Judge this against the SEARCH PREFERENCES only. The city on "
        "the candidate's resume is where they live, not a location they asked for, and a "
        "posting there does not count as a match unless the preferences name it."
    ),
    "logistics_blocker": (
        "The posting states a work-authorization, clearance, language or attendance "
        "constraint that the candidate's resume contradicts."
    ),
}

QUESTION_INSTRUCTIONS: Mapping[str, str] = {
    "hard_requirements": "How much of what this posting states as REQUIRED is evidenced in "
    "the candidate's resume? Judge only required items, not preferred ones.",
    "core_stack": "How much hands-on evidence does the resume show for the core technical "
    "stack this role is built around?",
    "years": "How does the candidate's own experience compare with the experience bar this "
    "posting states?",
    "level": "How closely does the candidate's seniority match the rung this posting is "
    "written for? Judge the rung, not the skills.",
    "nice_to_haves": "How many of the posting's preferred-but-not-required items are "
    "evidenced in the resume?",
}

_BAND_COUNT = 5


class DecisionsClient(Protocol):
    """The one surface this module uses to reach a decision model.

    Narrow on purpose: the module takes a client and never knows about HTTP, keys
    or which gateway served the request, exactly as the LLM path takes an engine.
    """

    def decide(self, body: dict[str, Any]) -> dict[str, Any]: ...


def build_request(
    master_md: str, job_text: str, preferences_md: str = ""
) -> dict[str, Any]:
    """The whole decision: 1 state, 5 score questions, 2 nouls."""
    state: dict[str, str] = {
        "candidate_resume": master_md.strip(),
        "job_posting": job_text.strip(),
    }
    questions: dict[str, Any] = {
        key: {
            "type": "score",
            "instructions": QUESTION_INSTRUCTIONS[key],
            "criteria": list(bands),
        }
        for key, bands in BANDS.items()
    }
    nouls = dict(NOULS)
    if preferences_md.strip():
        state["search_preferences"] = preferences_md.strip()
    else:
        # Without the block there is nothing to judge a location preference
        # against, and a guess is worse than an absent axis.
        nouls.pop("location_preference_met", None)
    questions.update(
        {key: {"type": "noul", "instructions": text} for key, text in nouls.items()}
    )
    return {"model": MODEL, "state": state, "questions": questions}


def _band_evidence(key: str, position: float) -> str:
    bands = BANDS[key]
    index = max(0, min(len(bands) - 1, round(position)))
    return bands[index]


def parse_answers(answers: Mapping[str, Any]) -> list[Factor]:
    """Jev's answers as typed factors, in `weights.py`'s own axis order."""
    factors: list[Factor] = []
    for key in BANDS:
        answer = answers.get(key)
        if not isinstance(answer, Mapping) or answer.get("score") is None:
            continue
        position = float(answer["score"])
        factors.append(
            Factor(
                key,
                max(0.0, min(1.0, position / (_BAND_COUNT - 1))),
                evidence=_band_evidence(key, position),
            )
        )
    location = answers.get("location_preference_met")
    if isinstance(location, Mapping) and location.get("noul") is not None:
        met = float(location["noul"])
        factors.append(
            Factor(
                "location_preference",
                met,
                kind="probability",
                evidence=f"{met:.0%} confidence the posting is in a location you asked for",
            )
        )
    blocker = answers.get("logistics_blocker")
    if isinstance(blocker, Mapping) and blocker.get("noul") is not None:
        blocked = float(blocker["noul"])
        factors.append(
            Factor(
                "logistics",
                1.0 - blocked,
                kind="probability",
                evidence=(
                    f"{blocked:.0%} confidence the posting states a constraint your resume "
                    "contradicts"
                ),
            )
        )
    return factors


def _reasons(factors: list[Factor]) -> list[str]:
    """2 to 4 plain lines, which is what the board renders (US-JB-05).

    Jev writes no prose, so these are built from the axes themselves: the
    strongest, the weakest, and whichever modifier axis has something to say.
    """
    weighted = [f for f in factors if f.factor in JEV_WEIGHTS]
    lines: list[str] = []
    if weighted:
        best = max(weighted, key=lambda f: f.value)
        worst = min(weighted, key=lambda f: f.value)
        lines.append(f"Strongest axis, {best.factor.replace('_', ' ')}: {best.evidence}")
        if worst.factor != best.factor:
            lines.append(f"Weakest axis, {worst.factor.replace('_', ' ')}: {worst.evidence}")
    for key in ("location_preference", "logistics"):
        found = next((f for f in factors if f.factor == key), None)
        if found is not None:
            lines.append(f"{key.replace('_', ' ').capitalize()}: {found.evidence}")
    if len(lines) < 2:
        lines.append("Scored on-device rubrics by a decision model; no prose is produced.")
    return lines[:4]


def _breakdown(factors: list[Factor], usage: Mapping[str, Any]) -> str:
    lines = ["Per-axis rubric positions from one decision call.", ""]
    for factor in factors:
        label = factor.factor.replace("_", " ")
        shape = "confidence" if factor.kind == "probability" else "rubric position"
        lines.append(f"- **{label}**: {factor.value:.2f} ({shape}). {factor.evidence}")
    cost = usage.get("cost")
    if cost is not None:
        lines += ["", f"Cost of this score: ${float(cost):.6f}."]
    return "\n".join(lines)


def score_jev(
    master_md: str,
    job_text: str,
    *,
    client: DecisionsClient,
    preferences_md: str = "",
) -> ScoreResult:
    """Score 1 posting with 1 decision call. Raises `ScoreError` on a bad reply.

    A posting under `MIN_JD_CHARS` is refused rather than scored, the same bar and
    the same reason as every other method: `deterministic.MIN_JD_CHARS`, imported
    rather than restated (duplication audit D-M18).
    """
    if len((job_text or "").strip()) < MIN_JD_CHARS:
        raise ScoreError(
            "job-input",
            f"posting is {len((job_text or '').strip())} chars, under the "
            f"{MIN_JD_CHARS}-char minimum to be scorable",
        )
    payload = client.decide(build_request(master_md, job_text, preferences_md))
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise ScoreError("parse", f"decision reply carried no answers: {str(payload)[:200]!r}")
    factors = parse_answers(answers)
    if not factors:
        raise ScoreError("parse", "decision reply answered none of the questions asked")
    usage = payload.get("usage") or {}
    return ScoreResult(
        score=weighted_total(factors, JEV_WEIGHTS),
        reasons=_reasons(factors),
        breakdown_md=_breakdown(factors, usage),
        factors=factors,
        usage=Usage(
            internal_calls=1,
            tokens_in=usage.get("prompt_tokens") or usage.get("input_tokens"),
            tokens_out=usage.get("completion_tokens") or usage.get("output_tokens"),
            usd=float(usage["cost"]) if usage.get("cost") is not None else None,
            model=MODEL,
        ),
    )
