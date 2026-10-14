"""Jev scoring: 1 request, 1 question per axis, and our own arithmetic on top.

The request shape and the reply shape are pinned here against a recorded reply,
because the module never sees HTTP and the vendor's own contract is the only thing
that can break underneath it.
"""

from __future__ import annotations

from typing import Any

import pytest

from sidecar.modules.scorer.jev import (
    BANDS,
    MODEL,
    NOULS,
    build_request,
    parse_answers,
    score_jev,
)
from sidecar.modules.scorer.types import ScoreError
from sidecar.modules.scorer.weights import JEV_WEIGHTS, LOCATION_MISS_PENALTY

RESUME = "Senior Software Engineer. 9 years of Python, Django, PostgreSQL and AWS."
POSTING = (
    "Senior Python Engineer. You will own Django services on AWS with PostgreSQL. "
    "Requirements: 6+ years, strong Python, Django and PostgreSQL. Nice to have: Go. "
    "Location: Bengaluru, India. You will review designs and mentor engineers."
)
PREFS = "Locations wanted: Bengaluru, Remote"


def reply(**overrides: Any) -> dict[str, Any]:
    """A reply in the shape the 2026-09-28 bench recorded, adapted to the 7 axes."""
    answers: dict[str, Any] = {
        "hard_requirements": {"score": 4.0, "confidence": 0.81},
        "core_stack": {"score": 3.6, "confidence": 0.74},
        "years": {"score": 4.0, "confidence": 0.9},
        "level": {"score": 3.8, "confidence": 0.7},
        "nice_to_haves": {"score": 1.0, "confidence": 0.6},
        "location_preference_met": {"noul": 0.96},
        "logistics_blocker": {"noul": 0.04},
    }
    answers.update(overrides)
    return {"answers": answers, "usage": {"cost": 0.000215, "prompt_tokens": 5129,
                                          "completion_tokens": 132}}


class FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else reply()
        self.bodies: list[dict[str, Any]] = []

    def decide(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        return self.payload


def test_one_request_carries_every_axis_as_its_own_question():
    body = build_request(RESUME, POSTING, PREFS)
    assert body["model"] == MODEL
    assert set(body["state"]) == {"candidate_resume", "job_posting", "search_preferences"}
    questions = body["questions"]
    # 5 graded axes as score questions, 2 binary ones as nouls, 1 request.
    assert [k for k, q in questions.items() if q["type"] == "score"] == list(BANDS)
    assert sorted(k for k, q in questions.items() if q["type"] == "noul") == sorted(NOULS)
    for key, bands in BANDS.items():
        assert questions[key]["criteria"] == list(bands)
        assert len(questions[key]["criteria"]) == 5
        assert questions[key]["instructions"]


def test_without_preferences_the_location_question_is_not_asked_at_all():
    """A guess is worse than an absent axis, which is the same rule the other
    methods follow for an axis they cannot assess."""
    body = build_request(RESUME, POSTING, "")
    assert "search_preferences" not in body["state"]
    assert "location_preference_met" not in body["questions"]
    assert "logistics_blocker" in body["questions"]


def test_a_rubric_position_becomes_a_graded_factor_and_a_noul_a_probability():
    factors = {f.factor: f for f in parse_answers(reply()["answers"])}
    assert factors["years"].value == 1.0
    assert factors["years"].kind == "graded"
    assert factors["core_stack"].value == pytest.approx(0.9)
    assert factors["core_stack"].evidence == BANDS["core_stack"][4]
    # The noul is a confidence, and it is labelled as one so no bar can read it
    # as a score (SCO-R7).
    assert factors["location_preference"].kind == "probability"
    assert factors["location_preference"].value == 0.96
    # The blocker question is inverted: a 4% chance of a blocker is a 0.96 axis.
    assert factors["logistics"].value == pytest.approx(0.96)
    assert factors["logistics"].kind == "probability"


def test_an_axis_the_model_did_not_answer_is_absent():
    answers = reply()["answers"]
    del answers["nice_to_haves"]
    del answers["location_preference_met"]
    keys = {f.factor for f in parse_answers(answers)}
    assert "nice_to_haves" not in keys
    assert "location_preference" not in keys
    assert "core_stack" in keys


def test_the_score_is_our_arithmetic_over_the_factors():
    result = score_jev(RESUME, POSTING, client=FakeClient(), preferences_md=PREFS)
    factors = {f.factor: f.value for f in result.factors}
    expected = round(
        sum(factors[k] * w for k, w in JEV_WEIGHTS.items()) / sum(JEV_WEIGHTS.values()) * 100
    )
    assert result.score == expected
    assert 0 <= result.score <= 100


def test_a_location_the_user_did_not_ask_for_costs_the_same_15_points():
    wanted = score_jev(RESUME, POSTING, client=FakeClient(), preferences_md=PREFS)
    unwanted = score_jev(
        RESUME,
        POSTING,
        client=FakeClient(reply(location_preference_met={"noul": 0.02})),
        preferences_md=PREFS,
    )
    assert wanted.score - unwanted.score == LOCATION_MISS_PENALTY


def test_a_missing_axis_renormalizes_instead_of_deflating_the_score():
    """An axis the model could not assess must not read as a 0, or a posting gets
    punished for what it failed to say."""
    full = score_jev(RESUME, POSTING, client=FakeClient(), preferences_md=PREFS)
    partial_reply = reply()
    del partial_reply["answers"]["nice_to_haves"]
    partial = score_jev(RESUME, POSTING, client=FakeClient(partial_reply), preferences_md=PREFS)
    # nice_to_haves was the weakest axis, so dropping it RAISES the score; the
    # point is that it is not treated as 0 weight 0.10.
    assert partial.score > full.score


def test_the_result_carries_prose_the_board_can_render_and_the_cost():
    result = score_jev(RESUME, POSTING, client=FakeClient(), preferences_md=PREFS)
    assert 2 <= len(result.reasons) <= 4
    assert all(line.strip() for line in result.reasons)
    assert "per-axis" in result.breakdown_md.lower()
    assert result.usage.usd == 0.000215
    assert result.usage.internal_calls == 1
    assert result.usage.model == MODEL


def test_a_thin_posting_is_refused_rather_than_scored():
    client = FakeClient()
    with pytest.raises(ScoreError) as ei:
        score_jev(RESUME, "too short to score", client=client)
    assert ei.value.stage == "job-input"
    assert client.bodies == []  # refused before spending anything


def test_a_reply_with_no_answers_is_a_parse_error_not_a_zero():
    with pytest.raises(ScoreError) as ei:
        score_jev(RESUME, POSTING, client=FakeClient({"usage": {}}))
    assert ei.value.stage == "parse"
    with pytest.raises(ScoreError):
        score_jev(RESUME, POSTING, client=FakeClient({"answers": {}}))
