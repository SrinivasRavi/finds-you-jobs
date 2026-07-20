"""CoverLetterer module tests — no live LLM (M1 playbook step 5).

Covers: skill-file invariants, comment stripping, prompt assembly + optional
blocks, input resolution, contract parsing + violations + REFUSED gate,
fake-engine end-to-end incl. {{DATE}} substitution, verbatim error propagation,
dry-run.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from sidecar.modules._shared.claude_engine import EngineError
from sidecar.modules._shared.completion_retry import MAX_ATTEMPTS
from sidecar.modules.coverletterer.coverletterer import cover, dry_run_prompt
from sidecar.modules.coverletterer.engine import Engine  # noqa: F401  (protocol import sanity)
from sidecar.modules.coverletterer.job_input import resolve_job
from sidecar.modules.coverletterer.output_parse import parse_output
from sidecar.modules.coverletterer.prompt import SKILL_PATH, build_user_prompt, load_skill
from sidecar.modules.coverletterer.types import CoverError, Usage

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

GOOD_OUTPUT = """===COVER_LETTER===
Jane Doe
Bengaluru | jane@example.com

Cover Letter: Senior Backend Engineer
Glean, Bengaluru — {{DATE}}

Seven years building distributed systems maps directly to your search platform work.

- **Cut p95 latency from 2.1s to 380ms,** by re-architecting the query path.
- **Migrated 40 services to Kubernetes,** reducing deploy time 70%.
===NOTES===
- Keywords mirrored: search platform, query path; could not fit naturally: none
- Gaps detected: none
- Drafting inputs: A-D derived defaults (no guidance)
- Word count: 380 (target 350-420)
"""


def test_skill_file_loads_and_carries_contract_and_guards():
    skill = load_skill()
    assert "===COVER_LETTER===" in skill and "===NOTES===" in skill
    assert "NEVER invent experience" in skill
    assert "REFUSED:" in skill  # JD gate present
    assert "350–420" in skill  # word band present
    assert "Undersell beats oversell" in skill  # [FYJ] addition present
    assert "no web access" in skill  # no-imported-company-knowledge rule


def test_skill_html_comments_are_stripped_from_prompt():
    skill = load_skill()
    assert "<!--" not in skill and "-->" not in skill
    assert "Distilled from career-ops" in SKILL_PATH.read_text()  # provenance stays in file


def test_prompt_includes_all_blocks_in_order():
    p = build_user_prompt("MASTER-X", "JD-Y", "GUIDE-Z", [("s.md", "SAMPLE-W")])
    assert p.index("MASTER RESUME") < p.index("MASTER-X") < p.index("JOB DESCRIPTION")
    assert p.index("JD-Y") < p.index("PER-JOB GUIDANCE") < p.index("GUIDE-Z")
    assert "WRITING SAMPLE: s.md" in p and "SAMPLE-W" in p


def test_prompt_omits_empty_optional_blocks():
    p = build_user_prompt("M", "J")
    assert "PER-JOB GUIDANCE" not in p and "WRITING SAMPLE" not in p


def test_resolve_job_reads_fixture_file():
    jd = resolve_job(str(FIXTURES / "jds" / "text" / "J01-glean-backend-bangalore.md"))
    assert len(jd) > 200


def test_resolve_job_rejects_fragments():
    with pytest.raises(CoverError) as ei:
        resolve_job("too short")
    assert ei.value.stage == "job-input"


def test_parse_output_roundtrip_and_fence_stripping():
    letter, notes = parse_output(GOOD_OUTPUT)
    assert letter.startswith("Jane Doe")
    assert "{{DATE}}" in letter  # substitution is the orchestrator's job, not the parser's
    assert len(notes) == 4
    fenced = f"```markdown\n{GOOD_OUTPUT}\n```"
    assert parse_output(fenced)[0].startswith("Jane Doe")


def test_parse_output_rejects_contract_violations_and_refusal():
    with pytest.raises(CoverError, match="contract"):
        parse_output("Dear hiring manager, ...")
    with pytest.raises(CoverError) as ei:
        parse_output(
            "===COVER_LETTER===\nREFUSED: no role title or responsibilities in input\n"
            "===NOTES===\n- refused per JD gate\n"
        )
    assert ei.value.stage == "jd-gate"
    assert "no role title" in str(ei.value)


class FakeEngine:
    def __init__(self, output: str = GOOD_OUTPUT) -> None:
        self.output = output
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        self.calls.append((system_prompt, user_prompt))
        return self.output, Usage(internal_calls=1, usd=0.01, model="fake")


class FailingEngine:
    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        raise CoverError("engine", "claude CLI exited 1: rate limited, retry later")


def test_cover_end_to_end_substitutes_date():
    fake = FakeEngine()
    result = cover("# Master\nlots of experience", "responsibilities " * 20, engine=fake)
    assert "{{DATE}}" not in result.cover_letter_md
    assert _dt.date.today().isoformat() in result.cover_letter_md
    assert len(result.notes) == 4
    system_prompt, user_prompt = fake.calls[0]
    assert "===COVER_LETTER===" in system_prompt  # skill is the system prompt
    assert "lots of experience" in user_prompt


def test_cover_surfaces_engine_error_verbatim():
    with pytest.raises(CoverError, match="rate limited, retry later"):
        cover("# Master", "responsibilities " * 20, engine=FailingEngine())


REFUSED_OUTPUT = (
    "===COVER_LETTER===\nREFUSED: no role title or responsibilities in input\n"
    "===NOTES===\n- refused per JD gate\n"
)


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


def test_cover_retries_transient_engine_error_and_succeeds():
    eng = FlakyThenGoodEngine(fail_times=1)
    result = cover("# Master", "responsibilities " * 20, engine=eng)
    assert result.cover_letter_md.startswith("Jane Doe")
    assert eng.calls == 2
    # A call that raised before returning has no usage to bill — only the
    # winning attempt's usage counts here.
    assert result.usage.usd == pytest.approx(0.01)


def test_cover_exhausts_retries_and_raises_last_engine_error():
    eng = FlakyThenGoodEngine(fail_times=MAX_ATTEMPTS)
    with pytest.raises(EngineError, match="empty content"):
        cover("# Master", "responsibilities " * 20, engine=eng)
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


def test_cover_retries_parse_contract_failure_and_sums_billed_usage():
    eng = BadThenGoodOutputEngine()
    result = cover("# Master", "responsibilities " * 20, engine=eng)
    assert eng.calls == 2
    # Cost honesty: the failed-but-billed first attempt's spend must not
    # vanish from the ledger just because the retry succeeded.
    assert result.usage.usd == pytest.approx(0.07)
    assert result.usage.internal_calls == 2
    assert result.usage.model == "fake"


class RefusingEngine:
    """A deliberate JD-gate refusal — must NEVER be retried (it's not a
    transient failure; re-asking the same prompt would just refuse again and
    burn another billed call for nothing)."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        self.calls += 1
        return REFUSED_OUTPUT, Usage(internal_calls=1, usd=0.01, model="fake")


def test_cover_jd_gate_refusal_is_not_retried():
    eng = RefusingEngine()
    with pytest.raises(CoverError) as ei:
        cover("# Master", "responsibilities " * 20, engine=eng)
    assert ei.value.stage == "jd-gate"
    assert eng.calls == 1  # no retry burned on a deliberate refusal


def test_dry_run_prompt_contains_skill_and_inputs_without_llm():
    p = dry_run_prompt("# Master resume body", "responsibilities " * 20, "lead with Kafka")
    assert "SYSTEM (skill)" in p and "USER" in p
    assert "cover-letter writing engine" in p
    assert "# Master resume body" in p and "lead with Kafka" in p
