"""The zero-LLM scorer: Settings > Scoring "Keyword scoring", and the grey floor
behind a failed AI score. See the module's own docstring for why the rubric is a
fresh design rather than a JustHireMe port."""

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
# A JD under MIN_JD_CHARS scores 0, a refusal carried in `reasons`. Overlap over
# the JD's own term count had description-less jobs averaging 53.0 against 44.9
# and outranking real ones.

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


# The 3 signals added 2026-10-01 (scoring.md sections 6.6 and 6.7). Measured on
# the 98 assessable bench postings, these moved the method's agreement with the
# Opus 5 reference from Spearman +0.170 to +0.484.

SENIOR_RESUME = (
    "Senior Software Engineer at Acme. 9 years of Python, Django, PostgreSQL "
    "and AWS. Built and operated production services."
)
BODY = (
    "Python engineer wanted to own Django services on AWS with PostgreSQL. "
    "Requirements: strong Python, Django, PostgreSQL and AWS experience."
)


def wanted_bengaluru():
    from sidecar.modules.geo.resolve import resolve_wanted

    return resolve_wanted(["Bengaluru", "Remote"])


def test_the_old_signature_still_works_and_emits_the_2_old_factors():
    """Every existing caller keeps compiling, and an axis it cannot assess is
    absent rather than guessed."""
    result = score_deterministic(SENIOR_RESUME, jd(BODY))
    assert {f.factor for f in result.factors} == {"core_stack", "years"}
    assert all(0.0 <= f.value <= 1.0 for f in result.factors)
    assert all(f.kind == "graded" for f in result.factors)


def test_a_location_the_user_did_not_ask_for_costs_15_points():
    here = score_deterministic(
        SENIOR_RESUME, jd(BODY), location="Bengaluru, India", wanted=wanted_bengaluru()
    )
    elsewhere = score_deterministic(
        SENIOR_RESUME, jd(BODY), location="Remote - USA", wanted=wanted_bengaluru()
    )
    assert here.score - elsewhere.score == 15
    assert [f.value for f in here.factors if f.factor == "location_preference"] == [1.0]
    assert [f.value for f in elsewhere.factors if f.factor == "location_preference"] == [0.0]
    assert any("Location:" in r for r in here.reasons)


def test_a_location_the_field_cannot_settle_costs_nothing_and_claims_nothing():
    """The honest third answer. 29 of the maintainer's 115 rows land here."""
    plain = score_deterministic(SENIOR_RESUME, jd(BODY))
    unreadable = score_deterministic(
        SENIOR_RESUME, jd(BODY), location="Remote; San Francisco", wanted=wanted_bengaluru()
    )
    assert unreadable.score == plain.score
    assert not [f for f in unreadable.factors if f.factor == "location_preference"]


def test_the_rung_the_title_states_is_scored_against_the_rung_the_resume_states():
    aligned = score_deterministic(SENIOR_RESUME, jd(BODY), title="Senior Software Engineer")
    mismatched = score_deterministic(SENIOR_RESUME, jd(BODY), title="Software Engineer Intern")
    assert aligned.score > mismatched.score
    assert [f.value for f in aligned.factors if f.factor == "level"] == [1.0]
    assert any("Seniority:" in r for r in aligned.reasons)


def test_an_unstated_level_drops_out_of_the_weighting_rather_than_scoring_zero():
    """23 of 93 real titles state no rung, and a scorer that read that as a bad
    rung would punish every posting that does not advertise one."""
    unstated = score_deterministic(SENIOR_RESUME, jd(BODY), title="Software Engineer, DevOps")
    assert not [f for f in unstated.factors if f.factor == "level"]
    assert unstated.score == score_deterministic(SENIOR_RESUME, jd(BODY)).score
    assert any("weight 60%" in r for r in unstated.reasons)


def test_a_resume_with_no_rung_leaves_the_level_axis_unassessed():
    result = score_deterministic("Python and Django developer.", jd(BODY), title="Staff Engineer")
    assert not [f for f in result.factors if f.factor == "level"]


def test_an_unassessable_posting_still_claims_no_factors():
    result = score_deterministic(SENIOR_RESUME, "too short", title="Senior Engineer")
    assert result.score == 0
    assert result.factors == []
