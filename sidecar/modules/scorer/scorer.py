"""The Scorer black box: score(master, job) to ScoreResult.

1 bounded operation; internals can change freely. The module owns no storage:
interim files live in a per-operation scratch dir deleted on return
(`keep_scratch=True` to debug)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable

from sidecar.modules._shared.completion_retry import complete_and_parse

from .engine import ClaudeCliEngine, Engine
from .job_input import resolve_job
from .output_parse import parse_output
from .prompt import build_user_prompt, load_skill
from .types import ScoreError, ScoreResult, Usage


def score(
    master_md: str,
    job: str,
    engine: Engine | None = None,
    keep_scratch: bool = False,
    skill_md: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    preferences_md: str | None = None,
) -> ScoreResult:
    """Score `master_md` against `job` (JD text, a path or a URL). `skill_md` replaces
    the skill file; `cancelled` raises `CompletionCancelled` (F-M7).
    `preferences_md` adds a third block that outranks the resume on where the user
    wants to work; without it the resume's address was the only location signal
    (S-C67)."""
    engine = engine or ClaudeCliEngine()
    jd_md = resolve_job(job)
    system_prompt = skill_md if skill_md is not None else load_skill()
    user_prompt = build_user_prompt(master_md, jd_md, preferences_md)

    scratch = tempfile.TemporaryDirectory(prefix="fyj-score-")
    try:
        # v0 engine needs no interim files; the scratch dir is the seam future
        # multi-step internals (checkpoints, sub-agent handoffs) write into.
        (value, reasons, breakdown_md, factors), usage = complete_and_parse(
            engine, system_prompt, user_prompt, parse_output, ScoreError, cancelled
        )
        return ScoreResult(
            score=value,
            reasons=reasons,
            breakdown_md=breakdown_md,
            factors=factors,
            usage=Usage(**usage),
        )
    finally:
        if not keep_scratch:
            scratch.cleanup()

