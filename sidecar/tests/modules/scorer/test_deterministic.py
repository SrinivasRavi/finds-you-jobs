"""Deterministic (zero-LLM) scorer — experiment branch only, not shipped on
main. See sidecar/modules/scorer/deterministic.py's module docstring for why
this is a fresh design, not a JustHireMe port."""

from __future__ import annotations

from sidecar.modules.scorer.deterministic import MIN_JD_CHARS, score_deterministic


def jd(body: str) -> str:
    """A JD fixture of realistic length. The rubric cases below used 77-char
    one-liners, which is shorter than any real posting (the shortest description
    on the maintainer's install is 464 chars) and now sits under `MIN_JD_CHARS`,
    where scoring refuses. `body` carries the signal each test is about; the
    filler is deliberately generic so it adds no skills, no years, and no
    red-flag terms of its own."""
    filler = (
        " The team ships to production continuously and reviews each other's work. "
        "You will collaborate with product and design, take part in on-call, and "
        "help shape how we build. We care about clear writing and steady delivery "
        "more than heroics, and we support conference attendance and study time."
    )
    text = body + filler
    assert len(text) >= MIN_JD_CHARS, "fixture must clear the too-thin-to-score floor"
    return text


def test_strong_skill_and_experience_match_scores_high():
    # Skill-dense on both sides, at realistic JD length. The generic `jd()` filler
    # is deliberately NOT used here: the rubric divides overlap by the JD's whole
    # term count, so culture-speak a resume can't match legitimately dilutes the
    # score. That dilution is why real postings top out near 68 on this rubric
    # (S-C27); this case is about a genuine match, so the JD reads like one.
    master_md = (
        "Senior backend engineer. 8 years experience with Python, Django, PostgreSQL, "
        "AWS, Celery, Redis, Docker, REST APIs, pytest, CI pipelines, mentoring."
    )
    job_text = (
        "Looking for a senior backend engineer with 5+ years experience in Python and "
        "Django. You will design PostgreSQL schemas, build REST APIs, run Celery workers "
        "backed by Redis, package services with Docker, and deploy to AWS. Strong pytest "
        "habits and CI pipelines expected, plus mentoring of junior backend engineers."
    )
    result = score_deterministic(master_md, job_text)
    assert result.score >= 65


def test_no_skill_overlap_scores_low():
    master_md = "Frontend designer. 3 years experience with Figma, CSS, accessibility."
    body = "Looking for a backend engineer with 5+ years experience in Rust and Kubernetes."
    result = score_deterministic(master_md, jd(body))
    assert result.score < 40


def test_experience_shortfall_lowers_score_but_skill_overlap_still_counts():
    master_md = "Junior engineer. 1 year experience with Python and Django."
    result = score_deterministic(
        master_md, jd("Looking for an engineer with 8+ years experience in Python and Django.")
    )
    assert 0 < result.score < 70  # real overlap, real experience gap — neither hides the other


def test_red_flag_term_caps_score_regardless_of_overlap():
    master_md = "Senior backend engineer. 8 years experience with Python, Django."
    body = "Backend role, Python, Django, 5+ years. Note: an unpaid position, for exposure."
    result = score_deterministic(master_md, jd(body))
    assert result.score <= 40
    assert any("red-flag" in r.lower() for r in result.reasons)


# --- the too-thin-to-score floor (S-C27) --------------------------------------
# A JD under MIN_JD_CHARS scores 0. Before this, the score divided overlap by the
# JD's OWN term count, so a 8-term title header scored on a handful of matches
# while a 250-term real JD was graded strictly: measured on the maintainer's
# install, description-less jobs averaged 53.0 against 44.9 for described ones
# and outranked them on the board. The 0 is a refusal to rate, carried in
# `reasons`, not a claim that the role is a bad fit.

RESUME = "Senior backend engineer. 8 years with Python, Django, PostgreSQL, AWS. Mumbai, India."


def test_empty_jd_scores_zero():
    # Replaces an earlier "no JD signal is neutral, not zero" case. A score is a
    # ranking claim, and a job we cannot assess must not outrank one we can.
    result = score_deterministic(RESUME, "")
    assert result.score == 0


def test_title_only_jd_scores_zero():
    # Exactly what `compose_job_text` produces for a job whose description never
    # got captured (S-A6): title, company, location, no body. 71 chars.
    header = "# Frontend Developer\n\nAMINA Bank · Mumbai, Maharashtra, India (On-site)"
    result = score_deterministic(RESUME, header)
    assert result.score == 0


def test_zero_score_says_why():
    result = score_deterministic(RESUME, "# Senior Software Engineer\n\nS&P Global · Mumbai, India")
    assert result.score == 0
    assert any("description" in r.lower() for r in result.reasons)


def test_company_and_city_tokens_cannot_carry_a_score():
    # The artefact this floor kills: every overlapping term here is location or
    # company text ("mumbai", "india", "global"), which says nothing about fit.
    # It used to score 70.
    header = "# Senior Software Engineer (.Net, Java)\n\nS&P Global · Mumbai, Maharashtra, India"
    assert score_deterministic("Mumbai, India. Global. 8 years.", header).score == 0


def test_a_real_jd_is_untouched_by_the_floor():
    # 464 chars is the SHORTEST real description in the maintainer's install, so
    # the floor must sit well below anything a genuine posting carries.
    jd = (
        "# Senior Backend Engineer\n\nAcme · Remote\n\n"
        + "We are looking for a senior backend engineer with 5+ years of experience "
        "building and operating Python services. You will own the Django monolith, "
        "design PostgreSQL schemas, and run workloads on AWS. Responsibilities "
        "include mentoring engineers, leading design reviews, and improving our "
        "deployment pipeline. Requirements: strong Python, Django, PostgreSQL and "
        "AWS experience, plus a track record of shipping production services."
    )
    assert len(jd) >= 464
    result = score_deterministic(RESUME, jd)
    assert result.score > 0
    assert any("overlap" in r.lower() for r in result.reasons)  # graded, not refused


def test_no_years_stated_is_neutral_not_penalized():
    # Isolates the YEARS criterion by holding skill overlap fixed: same JD body,
    # once with a bar the resume can't meet and once with none stated. Asserting
    # on the combined score would measure skill overlap instead, which the
    # realistic-length filler legitimately dilutes.
    master_md = "Backend engineer with 1 year of Python and Django experience."
    body = "Backend role needing Python and Django."
    unstated = score_deterministic(master_md, jd(body))
    barred = score_deterministic(master_md, jd(body + " Requires 10+ years."))
    assert unstated.score > barred.score
    assert any("unstated yrs required" in r.lower() for r in unstated.reasons)


def test_result_has_usage_with_no_llm_cost():
    result = score_deterministic("resume", jd("Backend engineer role."))
    assert result.usage.tokens_in is None
    assert result.usage.usd is None
    assert result.usage.internal_calls == 0
