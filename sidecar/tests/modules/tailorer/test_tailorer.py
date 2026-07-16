"""Tailorer silo tests — no live LLM calls anywhere in here."""

from __future__ import annotations

from pathlib import Path

import pytest

from sidecar.modules.tailorer.engine import Engine  # noqa: F401  (protocol import sanity)
from sidecar.modules.tailorer.job_input import resolve_job
from sidecar.modules.tailorer.output_parse import parse_output
from sidecar.modules.tailorer.prompt import (
    build_user_prompt,
    load_skill,
    load_writing_samples,
)
from sidecar.modules.tailorer.tailorer import dry_run_prompt, tailor
from sidecar.modules.tailorer.types import TailorError, Usage

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
MASTER = (FIXTURES / "master-resumes" / "master_resume_1.md").read_text()
JD_PATH = FIXTURES / "jds" / "text" / "J01-glean-backend-bangalore.md"


class FakeEngine:
    """Deterministic engine honoring the output contract."""

    def __init__(self, raw: str | None = None) -> None:
        self.raw = raw or (
            "===RESUME===\n# Tenet Loader\n\n## Professional Summary\n\nBackend engineer.\n"
            "===NOTES===\n- Keyword coverage: 12/18 JD keywords present; missing: golang\n"
            "- Archetype: backend platform\n"
        )
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        self.calls.append((system_prompt, user_prompt))
        return self.raw, Usage(internal_calls=1, model="fake")


# --- skill + prompt assembly -------------------------------------------------


def test_skill_file_loads_and_carries_contract_and_guards():
    skill = load_skill()
    assert "===RESUME===" in skill and "===NOTES===" in skill
    assert "NEVER invent experience" in skill
    assert "Tool-of-trade conflation" in skill
    assert "six-second clarity gate" in skill.lower()
    assert "Undersell beats oversell" in skill  # [FYJ] addition present


def test_skill_html_comments_are_stripped_from_prompt():
    # Authoring-side comments (provenance notes) must never reach the model.
    skill = load_skill()
    assert "<!--" not in skill and "-->" not in skill
    # G7 item 10 strict-ordering rule: enabled 2026-07-05, must reach the model.
    assert "STRICT reverse-chronological" in skill


def test_prompt_includes_all_blocks_in_order():
    p = build_user_prompt("MASTER-X", "JD-Y", "GUIDE-Z", [("s.md", "SAMPLE-W")])
    assert p.index("MASTER RESUME") < p.index("MASTER-X") < p.index("JOB DESCRIPTION")
    assert p.index("JD-Y") < p.index("PER-JOB GUIDANCE") < p.index("GUIDE-Z")
    assert "WRITING SAMPLE: s.md" in p and "SAMPLE-W" in p


def test_prompt_omits_empty_optional_blocks():
    p = build_user_prompt("M", "J")
    assert "PER-JOB GUIDANCE" not in p and "WRITING SAMPLE" not in p


def test_writing_samples_skip_readme(tmp_path: Path):
    (tmp_path / "README.md").write_text("skip me")
    (tmp_path / "cover-letter.md").write_text("my past letter")
    samples = load_writing_samples(tmp_path)
    assert [name for name, _ in samples] == ["cover-letter.md"]


# --- job input ---------------------------------------------------------------


def test_resolve_job_reads_fixture_file():
    jd = resolve_job(str(JD_PATH))
    assert "Software Engineer, Backend" in jd


def test_resolve_job_accepts_raw_text_and_rejects_fragments():
    long_jd = "We are hiring a backend engineer. " * 10
    assert resolve_job(long_jd) == long_jd
    with pytest.raises(TailorError, match="job-input"):
        resolve_job("too short")


# --- output contract ---------------------------------------------------------


def test_parse_output_roundtrip_and_fence_stripping():
    raw = "===RESUME===\nBODY\n===NOTES===\n- a\n- b\n"
    assert parse_output(raw) == ("BODY", ["a", "b"])
    fenced = f"```markdown\n{raw}\n```"
    assert parse_output(fenced)[0] == "BODY"


def test_parse_output_rejects_contract_violations():
    with pytest.raises(TailorError, match="parse"):
        parse_output("here is your resume, hope it helps!")
    with pytest.raises(TailorError, match="RESUME block is empty"):
        parse_output("===RESUME===\n\n===NOTES===\n- x\n")


# --- end-to-end through the black box (fake engine) --------------------------


def test_tailor_end_to_end_with_fake_engine():
    eng = FakeEngine()
    result = tailor(MASTER, str(JD_PATH), guidance="lead with auth work", engine=eng)
    assert result.resume_md.startswith("# Tenet Loader")
    assert any("Keyword coverage" in n for n in result.notes)
    assert result.usage.internal_calls == 1
    system_prompt, user_prompt = eng.calls[0]
    assert "tailor-resume-skill" in system_prompt
    assert "lead with auth work" in user_prompt
    assert "Software Engineer, Backend" in user_prompt  # JD actually resolved


def test_tailor_surfaces_engine_error_verbatim():
    class BoomEngine:
        def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
            raise TailorError("engine", "rate limited: try again in 60s")

    with pytest.raises(TailorError, match="rate limited: try again in 60s"):
        tailor(MASTER, str(JD_PATH), engine=BoomEngine())


def test_dry_run_prompt_contains_skill_and_inputs_without_llm():
    out = dry_run_prompt(MASTER, str(JD_PATH))
    assert "SYSTEM (skill)" in out and "Tenet Loader" in out and "Glean" in out or "glean" in out
