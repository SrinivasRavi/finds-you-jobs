"""Deterministic (zero-LLM) scorer — experiment branch only, not shipped on
main. See sidecar/modules/scorer/deterministic.py's module docstring for why
this is a fresh design, not a JustHireMe port."""

from __future__ import annotations

from sidecar.modules.scorer.deterministic import score_deterministic


def test_strong_skill_and_experience_match_scores_high():
    master_md = "Senior backend engineer. 8 years experience with Python, Django, PostgreSQL, AWS."
    job_text = "Looking for a backend engineer with 5+ years experience in Python and Django."
    result = score_deterministic(master_md, job_text)
    assert result.score >= 80


def test_no_skill_overlap_scores_low():
    master_md = "Frontend designer. 3 years experience with Figma, CSS, accessibility."
    job_text = "Looking for a backend engineer with 5+ years experience in Rust and Kubernetes."
    result = score_deterministic(master_md, job_text)
    assert result.score < 40


def test_experience_shortfall_lowers_score_but_skill_overlap_still_counts():
    master_md = "Junior engineer. 1 year experience with Python and Django."
    job_text = "Looking for an engineer with 8+ years experience in Python and Django."
    result = score_deterministic(master_md, job_text)
    assert 0 < result.score < 70  # real overlap, real experience gap — neither hides the other


def test_red_flag_term_caps_score_regardless_of_overlap():
    master_md = "Senior backend engineer. 8 years experience with Python, Django."
    job_text = "Backend role, Python, Django, 5+ years. Note: an unpaid position, for exposure."
    result = score_deterministic(master_md, job_text)
    assert result.score <= 40
    assert any("red-flag" in r.lower() for r in result.reasons)


def test_jd_with_no_extractable_terms_is_neutral_not_zero():
    result = score_deterministic("Some resume text.", "")
    assert result.score > 0  # no JD signal is not evidence of a bad match


def test_no_years_stated_is_neutral_not_penalized():
    master_md = "Backend engineer with Python and Django experience."
    job_text = "Backend role needing Python and Django."  # no explicit years bar
    result = score_deterministic(master_md, job_text)
    # High skill overlap, no experience penalty since the JD names no bar.
    assert result.score >= 70


def test_result_has_usage_with_no_llm_cost():
    result = score_deterministic("resume", "job")
    assert result.usage.tokens_in is None
    assert result.usage.usd is None
    assert result.usage.internal_calls == 0
