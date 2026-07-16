"""The Scorer black box: score(master, job) → ScoreResult.

One bounded operation at the interface (ROADMAP §4). Internals (engine, step
count, interim artifacts) are free to change without touching callers.

Storage stance (ROADMAP §4): the module owns no persistent storage. Interim
artifacts, if any step needs them, live in a per-operation scratch directory
that is deleted when the operation returns (pass keep_scratch=True to debug).
The final artifact is returned as a value; the caller (CLI now, app later)
decides where it lives.
"""

from __future__ import annotations

import tempfile

from .engine import ClaudeCliEngine, Engine
from .job_input import resolve_job
from .output_parse import parse_output
from .prompt import build_user_prompt, load_skill
from .types import ScoreResult


def score(
    master_md: str,
    job: str,
    engine: Engine | None = None,
    keep_scratch: bool = False,
    skill_md: str | None = None,
) -> ScoreResult:
    """Score `master_md` against `job` (raw JD text, a .md/.txt path, or a URL).

    `skill_md`, when provided, replaces the on-disk skill file as the system
    prompt (the app's user-editable-prompt override, §5). None → the default."""
    engine = engine or ClaudeCliEngine()
    jd_md = resolve_job(job)
    system_prompt = skill_md if skill_md is not None else load_skill()
    user_prompt = build_user_prompt(master_md, jd_md)

    scratch = tempfile.TemporaryDirectory(prefix="fyj-score-")
    try:
        # v0 engine needs no interim files; the scratch dir is the seam future
        # multi-step internals (checkpoints, sub-agent handoffs) write into.
        raw, usage = engine.complete(system_prompt, user_prompt)
        value, reasons, breakdown_md = parse_output(raw)
        return ScoreResult(score=value, reasons=reasons, breakdown_md=breakdown_md, usage=usage)
    finally:
        if not keep_scratch:
            scratch.cleanup()


def dry_run_prompt(master_md: str, job: str) -> str:
    """Assemble and return the full prompt without any LLM call (CLI --dry-run)."""
    jd_md = resolve_job(job)
    return (
        "########## SYSTEM (skill) ##########\n"
        + load_skill()
        + "\n########## USER ##########\n"
        + build_user_prompt(master_md, jd_md)
    )
