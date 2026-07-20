"""Scorer module tests — no live LLM (M1 playbook step 5).

Covers: skill-file invariants, comment stripping, prompt assembly, input
resolution, contract parsing + violations, fake-engine end-to-end, verbatim
error propagation, dry-run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidecar.modules._shared.claude_engine import EngineError
from sidecar.modules._shared.completion_retry import MAX_ATTEMPTS
from sidecar.modules.scorer.engine import Engine  # noqa: F401  (protocol import sanity)
from sidecar.modules.scorer.job_input import resolve_job
from sidecar.modules.scorer.output_parse import parse_output
from sidecar.modules.scorer.prompt import SKILL_PATH, build_user_prompt, load_skill
from sidecar.modules.scorer.scorer import dry_run_prompt, score
from sidecar.modules.scorer.types import ScoreError, Usage

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

GOOD_OUTPUT = """===SCORE===
82
===REASONS===
- 8 years Java/Spring matches the 5+ years backend requirement
- Missing: Rust listed as required; no master evidence
===BREAKDOWN===
Backend platform · Senior · Bengaluru (hybrid)

| Requirement (hard/nice) | Master evidence | Verdict |
|---|---|---|
| Java 5+ yrs (hard) | "8 years building Java services" | strong |
"""


def test_skill_file_loads_and_carries_contract_and_guards():
    skill = load_skill()
    assert "===SCORE===" in skill and "===REASONS===" in skill and "===BREAKDOWN===" in skill
    assert "NEVER invent evidence" in skill
    assert "rank, don't gate" in skill.lower() or "never gates" in skill.lower()
    assert "Adjacent experience counts as partial" in skill  # [FYJ] addition present
    assert "90–100" in skill  # rubric bands present


def test_skill_html_comments_are_stripped_from_prompt():
    skill = load_skill()
    assert "<!--" not in skill and "-->" not in skill
    assert "Distilled from career-ops" in SKILL_PATH.read_text()  # provenance stays in file


def test_prompt_includes_blocks_in_order():
    p = build_user_prompt("MASTER-X", "JD-Y")
    assert p.index("MASTER RESUME") < p.index("MASTER-X") < p.index("JOB DESCRIPTION")
    assert p.index("JOB DESCRIPTION") < p.index("JD-Y")
    assert "===SCORE===" in p  # contract reminder present


def test_resolve_job_reads_fixture_file():
    jd = resolve_job(str(FIXTURES / "jds" / "text" / "J01-glean-backend-bangalore.md"))
    assert len(jd) > 200


def test_resolve_job_accepts_raw_text_and_rejects_fragments():
    long_text = "responsibilities " * 20
    assert resolve_job(long_text) == long_text
    with pytest.raises(ScoreError) as ei:
        resolve_job("too short")
    assert ei.value.stage == "job-input"


def test_parse_output_roundtrip_and_fence_stripping():
    value, reasons, breakdown = parse_output(GOOD_OUTPUT)
    assert value == 82
    assert reasons == [
        "8 years Java/Spring matches the 5+ years backend requirement",
        "Missing: Rust listed as required; no master evidence",
    ]
    assert breakdown.startswith("Backend platform")
    fenced = f"```markdown\n{GOOD_OUTPUT}\n```"
    assert parse_output(fenced)[0] == 82


def test_parse_output_rejects_contract_violations():
    with pytest.raises(ScoreError, match="contract"):
        parse_output("Here is my analysis of the job fit...")
    with pytest.raises(ScoreError, match="integer"):
        parse_output(GOOD_OUTPUT.replace("82", "very high"))
    with pytest.raises(ScoreError, match="0–100"):
        parse_output(GOOD_OUTPUT.replace("82", "120"))
    one_reason = GOOD_OUTPUT.replace(
        "- Missing: Rust listed as required; no master evidence\n", ""
    )
    with pytest.raises(ScoreError, match="2–4"):
        parse_output(one_reason)
    with pytest.raises(ScoreError, match="BREAKDOWN block is empty"):
        parse_output(GOOD_OUTPUT.split("===BREAKDOWN===")[0] + "===BREAKDOWN===\n\n")


class FakeEngine:
    def __init__(self, output: str = GOOD_OUTPUT) -> None:
        self.output = output
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        self.calls.append((system_prompt, user_prompt))
        return self.output, Usage(internal_calls=1, usd=0.01, model="fake")


class FailingEngine:
    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        raise ScoreError("engine", "claude CLI exited 1: rate limited, retry later")


def test_score_end_to_end_with_fake_engine():
    fake = FakeEngine()
    result = score("# Master\nlots of experience", "responsibilities " * 20, engine=fake)
    assert result.score == 82
    assert len(result.reasons) == 2
    assert "Requirement" in result.breakdown_md
    assert result.usage.model == "fake"
    system_prompt, user_prompt = fake.calls[0]
    assert "===SCORE===" in system_prompt  # skill is the system prompt
    assert "lots of experience" in user_prompt


def test_score_surfaces_engine_error_verbatim():
    with pytest.raises(ScoreError, match="rate limited, retry later"):
        score("# Master", "responsibilities " * 20, engine=FailingEngine())


class FlakyThenGoodEngine:
    """Raises a bare EngineError (an empty-content-style provider hiccup) the
    first `fail_times` calls, then returns GOOD_OUTPUT."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise EngineError("LLM API returned empty content")
        return GOOD_OUTPUT, Usage(
            internal_calls=1, tokens_in=10, tokens_out=5, usd=0.01, model="fake"
        )


def test_score_retries_transient_engine_error_and_succeeds():
    eng = FlakyThenGoodEngine(fail_times=1)
    result = score("# Master", "responsibilities " * 20, engine=eng)
    assert result.score == 82
    assert eng.calls == 2  # one failed attempt + the successful retry
    # A call that raised before returning has no usage to bill — only the
    # winning attempt's usage counts here.
    assert result.usage.usd == pytest.approx(0.01)
    assert result.usage.internal_calls == 1


def test_score_exhausts_retries_and_raises_last_engine_error():
    eng = FlakyThenGoodEngine(fail_times=MAX_ATTEMPTS)
    with pytest.raises(EngineError, match="empty content"):
        score("# Master", "responsibilities " * 20, engine=eng)
    assert eng.calls == MAX_ATTEMPTS


class BadThenGoodOutputEngine:
    """Returns a contract-violating response once (billed — a real completion
    happened), then GOOD_OUTPUT."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        self.calls += 1
        if self.calls == 1:
            return "not the contract", Usage(
                internal_calls=1, tokens_in=10, tokens_out=3, usd=0.02
            )
        return GOOD_OUTPUT, Usage(
            internal_calls=1, tokens_in=10, tokens_out=40, usd=0.05, model="fake"
        )


def test_score_retries_parse_contract_failure_and_sums_billed_usage():
    eng = BadThenGoodOutputEngine()
    result = score("# Master", "responsibilities " * 20, engine=eng)
    assert eng.calls == 2
    # Cost honesty: the failed-but-billed first attempt's spend must not
    # vanish from the ledger just because the retry succeeded.
    assert result.usage.usd == pytest.approx(0.07)
    assert result.usage.tokens_in == 20
    assert result.usage.tokens_out == 43
    assert result.usage.internal_calls == 2
    assert result.usage.model == "fake"  # the winning attempt's model


def test_dry_run_prompt_contains_skill_and_inputs_without_llm():
    p = dry_run_prompt("# Master resume body", "responsibilities " * 20)
    assert "SYSTEM (skill)" in p and "USER" in p
    assert "job-fit scoring engine" in p
    assert "# Master resume body" in p


def test_parse_output_truncates_excess_reasons_instead_of_failing():
    # 2026-07-17 dogfood: a 5-bullet REASONS burned the whole (paid) call as a
    # parse failure. The model orders reasons by importance — keep the first 4.
    six = GOOD_OUTPUT.replace(
        "===BREAKDOWN===",
        "- extra reason three\n- extra reason four\n- extra reason five\n===BREAKDOWN===",
    )
    value, reasons, _ = parse_output(six)
    assert value == 82
    assert len(reasons) == 4
    assert reasons[0] == "8 years Java/Spring matches the 5+ years backend requirement"


def test_parse_output_still_rejects_too_few_reasons():
    one = GOOD_OUTPUT.replace(
        "- Missing: Rust listed as required; no master evidence\n", ""
    )
    with pytest.raises(ScoreError) as ei:
        parse_output(one)
    assert "2–4 bullets" in str(ei.value)
